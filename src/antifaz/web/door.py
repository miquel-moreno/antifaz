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
3. The public routes, exact (path, method): GET and POST /panel/login.
4. Anything else needs a live session, read from the cookie of the request's scheme only
   (cookies.py). Without one, GET /panel answers 303 to the fixed relative "/panel/login" and
   every other path 401.

The router then runs with a PanelContext in scope["state"], so a route never parses the
cookie or the scheme again.

Concurrency: the session store and the login limiter have no locks. They are correct only
because every panel route is `async def` on the one event loop and nothing awaits between a
read and its write (tests/unit/test_web_concurrency.py).

Forbidden: asking for or reading the API key here. Taking the scheme from X-Forwarded-Proto
of an untrusted peer. Building the redirect from the Host header. Logging a cookie.
"""

from dataclasses import dataclass, field

from starlette.responses import HTMLResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from antifaz.http_errors import PanelInternalError, PanelUnauthorizedError, error_response
from antifaz.routing import route_path
from antifaz.web.cookies import read_session_cookie
from antifaz.web.forwarded import IPNetwork, effective_scheme, is_local_host, request_host
from antifaz.web.headers import with_security_headers
from antifaz.web.login_limit import FailureLimiter
from antifaz.web.pages import HTTPS_ONLY_PAGE
from antifaz.web.sessions import Session, SessionStore

# Served without a session. Exact pairs: "/panel/login/" or HEAD need a session.
PUBLIC_ROUTES = frozenset({("/panel/login", "GET"), ("/panel/login", "POST")})
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
        scheme = effective_scheme(scope, self._state.trusted_proxies)
        host = request_host(scope)
        if scheme != "https" and not is_local_host(host):
            await HTMLResponse(HTTPS_ONLY_PAGE, status_code=403)(scope, receive, send)
            return
        context = PanelContext(scheme, host, read_session_cookie(scope["headers"], scheme))
        if (path, scope["method"]) not in PUBLIC_ROUTES:
            session = self._state.sessions.get(context.session_id) if context.session_id else None
            if session is None:
                await self._no_session(path, scope["method"])(scope, receive, send)
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
