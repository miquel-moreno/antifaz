"""The panel's door (ADR-0018): every /panel* request goes through here, and only those.

api/gate.py hands over every path that is_panel_path() accepts (on the path without the ASGI
root_path, which is also what this door compares) and never asks for the API key on them: the
key does not open the panel. In this order:

1. Every answer of /panel*, errors included, gets the security headers (headers.py). An
   unexpected error inside gets a fixed 500 JSON with them, if nothing was sent yet, and is
   raised again so the server still logs it once.
2. HTTPS or localhost: the scheme is scope["scheme"], or X-Forwarded-Proto from a trusted
   proxy (forwarded.effective_scheme); "localhost" is decided by the Host header. Plain HTTP
   from any other host gets a fixed 403 page and goes no further (no login, no cookie).
3. GET and HEAD are safe; any other method (POST, PUT, DELETE, PATCH, OPTIONS...) on any
   /panel* path gets the CSRF checks, all in this one place and closed by default (csrf.py):
   a. Layer 3 and JSON (one Sec-Fetch-Site: same-origin, one Origin equal to scheme://host, a
      JSON Content-Type), else 403 csrf_rejected, BEFORE the session is looked up: a request
      from another origin never refreshes a session's last use.
   b. A live session (step 5), else 401.
   c. Layer 2: the session's CSRF token in exactly one X-Antifaz-CSRF header, else the same
      403 csrf_rejected as in (a). Layer 1, SameSite=Strict, is on the cookie.
4. The public routes, exact (path, method): GET /panel/login, and POST /panel/login, the only
   route exempt from (b) and (c) (CSRF_TOKEN_EXEMPT): it has no session yet.
5. Anything else needs a live session of the request's scheme, read from the cookie of that
   scheme only (cookies.py) and bound to it in the store (a session made over the other scheme
   is closed, sessions.py). Without one, GET /panel answers 303 to the fixed relative
   "/panel/login" and everything else 401.

The router then runs with a PanelContext in scope["state"], so a route never parses the
cookie or the scheme again.

Concurrency: the session store and the login limiter have no locks. They are correct only
because every panel route is `async def` on the one event loop and nothing awaits between a
read and its write (tests/unit/test_web_concurrency.py).

There is never a CORS header: an OPTIONS preflight from another origin gets 403.

Forbidden: asking for or reading the API key here. Adding a route to CSRF_TOKEN_EXEMPT (a test
fails if any route but the login skips the token). Taking the scheme from X-Forwarded-Proto
of an untrusted peer. Building the redirect from the Host header. Logging a cookie.
"""

from dataclasses import dataclass, field

from starlette.responses import HTMLResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from antifaz.http_errors import (
    CsrfRejectedError,
    PanelInternalError,
    PanelUnauthorizedError,
    error_response,
)
from antifaz.routing import route_path
from antifaz.web.cookies import read_session_cookie, session_scheme
from antifaz.web.csrf import has_csrf_token, is_json, is_same_origin
from antifaz.web.forwarded import IPNetwork, effective_scheme, is_local_host, request_host
from antifaz.web.headers import with_security_headers
from antifaz.web.login_limit import FailureLimiter
from antifaz.web.pages import HTTPS_ONLY_PAGE
from antifaz.web.sessions import Session, SessionStore

# Served without a session. Exact pairs: "/panel/login/" or HEAD need a session.
PUBLIC_ROUTES = frozenset({("/panel/login", "GET"), ("/panel/login", "POST")})
# The methods that change nothing: no CSRF check. Everything else gets all the layers.
SAFE_METHODS = frozenset({"GET", "HEAD"})
# Not safe, yet without the token layer (it keeps layer 3 and JSON). Only the login, ever.
CSRF_TOKEN_EXEMPT = frozenset({("/panel/login", "POST")})
LOGIN_PATH = "/panel/login"
CONTEXT_KEY = "antifaz.panel"


@dataclass(frozen=True)
class PanelState:
    """Everything the panel gets from the gateway: the token's fingerprint, never the token."""

    token_fingerprint: bytes = field(repr=False)
    trusted_proxies: tuple[IPNetwork, ...]
    sessions: SessionStore
    limiter: FailureLimiter


@dataclass(frozen=True)
class PanelContext:
    """What the door decided about one request, for the routes."""

    scheme: str
    host: str | None
    session_id: str | None = field(repr=False)  # the cookie of this scheme, live or not
    session: Session | None = None  # set only on the routes that need a session


def panel_context(scope: Scope) -> PanelContext:
    """What the door decided. Without it (a route reached some other way), 401: fail closed."""
    context = scope.get("state", {}).get(CONTEXT_KEY)
    if not isinstance(context, PanelContext):
        raise PanelUnauthorizedError()
    return context


class _SecuredSend:
    """`send` with the security headers on the response start; remembers if it started."""

    def __init__(self, send: Send) -> None:
        self._send = send
        self.started = False

    async def __call__(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.started = True
            headers = with_security_headers(message.get("headers", []))
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

    async def _handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = route_path(scope)
        method = scope["method"]
        headers = scope["headers"]
        scheme = effective_scheme(scope, self._state.trusted_proxies)
        host = request_host(scope)
        if scheme != "https" and not is_local_host(host):
            await HTMLResponse(HTTPS_ONLY_PAGE, status_code=403)(scope, receive, send)
            return
        safe = method in SAFE_METHODS
        # Layer 3 and JSON before the session: a cross-origin request never touches one.
        if not safe and not (is_same_origin(headers, scheme, host) and is_json(headers)):
            await error_response(CsrfRejectedError())(scope, receive, send)
            return
        context = PanelContext(scheme, host, read_session_cookie(headers, scheme))
        route = (path, method)
        public = route in PUBLIC_ROUTES and (safe or route in CSRF_TOKEN_EXEMPT)
        if not public:
            session = None
            if context.session_id is not None:
                session = self._state.sessions.get(context.session_id, session_scheme(scheme))
            if session is None:
                await self._no_session(path, method)(scope, receive, send)
                return
            needs_token = not safe and route not in CSRF_TOKEN_EXEMPT
            if needs_token and not has_csrf_token(headers, session.csrf_token):
                await error_response(CsrfRejectedError())(scope, receive, send)
                return
            context = PanelContext(scheme, host, context.session_id, session)
        scope.setdefault("state", {})[CONTEXT_KEY] = context
        await self.app(scope, receive, send)

    @staticmethod
    def _no_session(path: str, method: str) -> Response:
        if path == "/panel" and method == "GET":
            # A fixed relative URL: never built from the Host header or the request.
            return Response(status_code=303, headers={"Location": LOGIN_PATH})
        return error_response(PanelUnauthorizedError())
