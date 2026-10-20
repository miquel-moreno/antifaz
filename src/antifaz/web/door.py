"""The panel's door (ADR-0018): every /panel* request goes through here, and only those.

api/gate.py hands over every path that is_panel_path() accepts (on the path without the ASGI
root_path, which is also what this door compares) and never asks for the API key on them: the
key does not open the panel. In this order:

1. Every answer of /panel*, errors included, gets the security headers (headers.py). An
   unexpected error inside gets a fixed 500 JSON with them, if nothing was sent yet, and is
   raised again so the server still logs it once. Cache-Control is decided here, per answer:
   `no-store` for everything, except a 200 of a public static file (step 4), which gets
   `public, max-age=31536000, immutable`. Errors under /panel/static/ stay `no-store`.
2. HTTPS or localhost: the scheme is scope["scheme"], or X-Forwarded-Proto from a trusted
   proxy (forwarded.effective_scheme); "localhost" is decided by the Host header. Plain HTTP
   from any other host gets a fixed 403 page and goes no further (no login, no cookie).
2b. /panel/api/* (11c-3b review): a browser request whose Sec-Fetch-Site is not same-origin
   (another site, another localhost port, a navigation typed in the address bar) gets 403
   csrf_rejected, whatever the method and BEFORE the session is looked up
   (csrf.api_fetch_allowed). Without the header (not a browser) the usual rules follow. GET
   /panel keeps its rule: any site may open the page, and only its own requests count as a use.
3. GET and HEAD are safe; any other method (POST, PUT, DELETE, PATCH, OPTIONS...) on any
   /panel* path gets the CSRF checks, all in this one place and closed by default (csrf.py):
   a. Layer 3 and JSON (one Sec-Fetch-Site: same-origin, one Origin equal to scheme://host, a
      JSON Content-Type), else 403 csrf_rejected, BEFORE the session is looked up: a request
      from another origin never refreshes a session's last use.
   b. A live session (step 5), else 401.
   c. Layer 2: the session's CSRF token in exactly one X-Antifaz-CSRF header, else the same
      403 csrf_rejected as in (a). Layer 1, SameSite=Strict, is on the cookie.
4. The public routes, exact (path, method), served without a session: GET /panel/login and
   the routes of CSRF_TOKEN_EXEMPT, the only ones that skip (b) and (c). That list holds only
   POST /panel/login (it has no session yet), and PUBLIC_ROUTES is built from it, so no other
   route that is not safe can ever skip the token. And the static files (11c-1): only GET, and
   only a path that is exactly one of the manifest (assets.py), /panel/static/<name>.<hash>.ext.
   Any other name under /panel/static/ (the plain name, a licence text, a trick) is not public
   and follows step 5: 401 without a session, the route's 404 with one. A public static
   request never looks the session up.
5. Anything else needs a live session of the request's scheme, read from the cookie of that
   scheme only (cookies.py) and bound to it in the store (a session made over the other scheme
   is closed, sessions.py). It is looked up WITHOUT counting a use (peek). Without one, GET
   /panel answers 303 to the fixed relative "/panel/login" and everything else 401.
6. Only once every check of the request passed, and only for a request of the panel's own
   page (Sec-Fetch-Site same-origin, none or absent: csrf.counts_as_use), the session's last
   use is refreshed (touch). A refused request, or a page of another localhost port (same-site,
   which carries the cookie), never keeps a session alive.

The router then runs with a PanelContext in scope["state"], so a route never parses the
cookie or the scheme again.

Concurrency: the session store and the login limiter have no locks. They are correct only
because every panel route is `async def` on the one event loop and nothing awaits between a
read and its write. Here, nothing awaits from peek() to touch() (test_web_concurrency.py).

There is never a CORS header: an OPTIONS preflight from another origin gets 403.

Forbidden: asking for or reading the API key here. Adding a route to CSRF_TOKEN_EXEMPT (a test
fails if any route but the login skips the token). Taking the scheme from X-Forwarded-Proto
of an untrusted peer. Building the redirect from the Host header. Logging a cookie.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from starlette.responses import HTMLResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from antifaz.http_errors import (
    CsrfRejectedError,
    PanelInternalError,
    PanelUnauthorizedError,
    error_response,
)
from antifaz.routing import route_path
from antifaz.web.assets import Assets
from antifaz.web.cookies import read_session_cookie, session_scheme
from antifaz.web.csrf import (
    api_fetch_allowed,
    counts_as_use,
    has_csrf_token,
    is_json,
    is_same_origin,
)
from antifaz.web.forwarded import IPNetwork, effective_scheme, is_local_host, request_host
from antifaz.web.headers import IMMUTABLE, NO_STORE, with_security_headers
from antifaz.web.login_limit import FailureLimiter
from antifaz.web.pages import HTTPS_ONLY_PAGE
from antifaz.web.sessions import Session, SessionStore
from antifaz.web.status import StatusFacts
from antifaz.web.try_limit import TryLimiter

# The methods that change nothing: no CSRF check. Everything else gets all the layers.
SAFE_METHODS = frozenset({"GET", "HEAD"})
# Not safe, yet without the session and token layers (it keeps layer 3 and JSON). Only the
# login, ever: a test walks every registered route to keep it so.
CSRF_TOKEN_EXEMPT = frozenset({("/panel/login", "POST")})
# Served without a session: the login page, and the exempt routes. Exact pairs: "/panel/login/"
# or HEAD need a session. The static files have their own rule (is_public_static).
PUBLIC_ROUTES = frozenset({("/panel/login", "GET")}) | CSRF_TOKEN_EXEMPT
LOGIN_PATH = "/panel/login"
# The panel's JSON routes (11c-3b): only the panel's own fetch may read them (step 2b).
API_PREFIX = "/panel/api/"
CONTEXT_KEY = "antifaz.panel"
# 11e-1 (red stub): requests that never count as a use of the session.
NOT_A_USE: frozenset[tuple[str, str]] = frozenset()

# «Prueba un texto» (11d-a): text, language and whether to use the name detector (11d-b) in, the
# answer (web/try_response.py, without `ner`) out. The gateway builds it (api/app.py _panel):
# run_try() in a worker thread, behind the Try limiter (one thread) and then the proxy's mask
# limiter. The panel never sees the detector, the policy or the settings, only this callable.
type TryRunner = Callable[[str, str, bool], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class PanelState:
    """Everything the panel gets from the gateway: the token's fingerprint, never the token,
    the status facts (web/status.py) and the Try runner, never the settings."""

    token_fingerprint: bytes = field(repr=False)
    trusted_proxies: tuple[IPNetwork, ...]
    sessions: SessionStore
    limiter: FailureLimiter
    assets: Assets  # the static files' manifest: its exact paths are the public ones
    status: StatusFacts  # booleans, counts, enums, the version and checked hosts (11c-3b)
    try_runner: TryRunner = field(repr=False)  # «Prueba un texto»: the real pipeline (11d-a)
    try_limiter: TryLimiter = field(repr=False)  # 30 tries per minute per session (11d-a)
    # 11d-b: the words (NER tokens) a Try may give the name detector, None when a Try never uses
    # it (the NER is off). The page shows this number; the gateway derives it (api/app.py).
    try_ner_words: int | None = None


@dataclass(frozen=True)
class PanelContext:
    """What the door decided about one request, for the routes."""

    scheme: str
    host: str | None
    session_id: str | None = field(repr=False)  # the cookie of this scheme, live or not
    session: Session | None = None  # set only on the routes that need a session


def is_public_static(path: str, method: str, assets: Assets) -> bool:
    """GET of exactly a manifest path: the only static request served without a session."""
    return method == "GET" and path in assets.paths


def panel_context(scope: Scope) -> PanelContext:
    """What the door decided. Without it (a route reached some other way), 401: fail closed."""
    context = scope.get("state", {}).get(CONTEXT_KEY)
    if not isinstance(context, PanelContext):
        raise PanelUnauthorizedError()
    return context


class _SecuredSend:
    """`send` with the security headers on the response start; remembers if it started.

    The door sets `cacheable` once a request is a public static file: only then, and only for
    a 200, Cache-Control is IMMUTABLE. Every other answer gets NO_STORE."""

    def __init__(self, send: Send) -> None:
        self._send = send
        self.started = False
        self.cacheable = False

    async def __call__(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.started = True
            cache = IMMUTABLE if self.cacheable and message["status"] == 200 else NO_STORE
            headers = with_security_headers(message.get("headers", []), cache)
            message = {**message, "headers": headers}
        await self._send(message)


class PanelDoor:
    def __init__(self, app: ASGIApp, state: PanelState) -> None:
        self.app = app
        self._state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        secured = _SecuredSend(send)
        try:
            await self._handle(scope, receive, secured)
        except Exception:
            if not secured.started:
                await error_response(PanelInternalError())(scope, receive, secured)
            raise  # the server logs it once; nothing more is sent (the response started)

    async def _handle(self, scope: Scope, receive: Receive, send: _SecuredSend) -> None:
        path = route_path(scope)
        method = scope["method"]
        headers = scope["headers"]
        scheme = effective_scheme(scope, self._state.trusted_proxies)
        host = request_host(scope)
        if scheme != "https" and not is_local_host(host):
            await HTMLResponse(HTTPS_ONLY_PAGE, status_code=403)(scope, receive, send)
            return
        if path.startswith(API_PREFIX) and not api_fetch_allowed(headers):
            await error_response(CsrfRejectedError())(scope, receive, send)
            return
        safe = method in SAFE_METHODS
        # Layer 3 and JSON before the session: a cross-origin request never touches one.
        if not safe and not (is_same_origin(headers, scheme, host) and is_json(headers)):
            await error_response(CsrfRejectedError())(scope, receive, send)
            return
        context = PanelContext(scheme, host, read_session_cookie(headers, scheme))
        if is_public_static(path, method, self._state.assets):
            send.cacheable = True  # a 200 of it may be kept; the session is never looked up
        elif (path, method) not in PUBLIC_ROUTES:
            # From peek() to touch(), plain code: the answer is sent only afterwards.
            sessions = self._state.sessions
            session = None
            if context.session_id is not None:
                session = sessions.peek(context.session_id, session_scheme(scheme))
            refused: Response | None = None
            if session is None:
                refused = self._no_session(path, method)
            elif not safe and not has_csrf_token(headers, session.csrf_token):
                refused = error_response(CsrfRejectedError())
            else:
                # Every check passed: only now, and only for the panel's own page, it is a use.
                if counts_as_use(headers):
                    session = sessions.touch(session.fingerprint) or session
                context = PanelContext(scheme, host, context.session_id, session)
            if refused is not None:
                await refused(scope, receive, send)
                return
        scope.setdefault("state", {})[CONTEXT_KEY] = context
        await self.app(scope, receive, send)

    @staticmethod
    def _no_session(path: str, method: str) -> Response:
        if path == "/panel" and method == "GET":
            # A fixed relative URL: never built from the Host header or the request.
            return Response(status_code=303, headers={"Location": LOGIN_PATH})
        return error_response(PanelUnauthorizedError())
