"""The panel's door (ADR-0018): every /panel* request goes through here, and only those.

api/gate.py hands over every path that is_panel_path() accepts and never asks for the API key
on them: the key does not open the panel. In this order:

1. Every answer of /panel*, errors included, gets the security headers (headers.py).
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

from fastapi.responses import HTMLResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from antifaz.api.errors import PanelUnauthorizedError, error_response
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
    context = scope["state"][CONTEXT_KEY]
    if not isinstance(context, PanelContext):  # pragma: no cover - only the door sets it
        raise TypeError("the panel door did not run")
    return context


def _secured(send: Send) -> Send:
    async def secured_send(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = with_security_headers(message.get("headers", []))
            message = {**message, "headers": headers}
        await send(message)

    return secured_send


class PanelDoor:
    def __init__(self, app: ASGIApp, state: PanelState) -> None:
        self.app = app
        self._state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        send = _secured(send)
        scheme = effective_scheme(scope, self._state.trusted_proxies)
        host = request_host(scope)
        if scheme != "https" and not is_local_host(host):
            await HTMLResponse(HTTPS_ONLY_PAGE, status_code=403)(scope, receive, send)
            return
        context = PanelContext(scheme, host, read_session_cookie(scope["headers"], scheme))
        if (scope["path"], scope["method"]) not in PUBLIC_ROUTES:
            session = self._state.sessions.get(context.session_id) if context.session_id else None
            if session is None:
                await self._no_session(scope)(scope, receive, send)
                return
            context = PanelContext(scheme, host, context.session_id, session)
        scope.setdefault("state", {})[CONTEXT_KEY] = context
        await self.app(scope, receive, send)

    @staticmethod
    def _no_session(scope: Scope) -> Response:
        if scope["path"] == "/panel" and scope["method"] == "GET":
            # A fixed relative URL: never built from the Host header or the request.
            return Response(status_code=303, headers={"Location": LOGIN_PATH})
        return error_response(PanelUnauthorizedError())
