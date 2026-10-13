"""The panel's routes for 11b-2a (ADR-0018): getting in. Every route is `async def`.

- GET /panel/login: the login page (public).
- POST /panel/login: the admin token as JSON (public, CSRF layer 3). In this order:
  1. Same origin and JSON (csrf.py), else 403; it does not count as a failure.
  2. The body, read as a stream and never past 1 KiB, with or without Content-Length, within
     LOGIN_BODY_SECONDS (5 s). A client that disconnects or is too slow gets the same fixed
     401 and it does NOT count: nobody's token was tried, and a slow line must not lock the
     administrator out.
  3. The attempt limit for the client's address (forwarded.client_ip), BEFORE comparing:
     blocked is 429 with Retry-After, even with the right token, and nothing is compared.
  4. Exactly {"token": "<at most 256 characters>"} (no repeated keys), and its SHA-256
     compared with the token's fingerprint by hmac.compare_digest. Anything wrong (body, size,
     token) is one byte-identical 401, and it counts.
  5. A session cookie that came in is closed (no session fixation), a new session is made and
     the cookie of the request's scheme is set.
- GET /panel: a placeholder for the interface of 11c, with the session's CSRF token in a meta.

Concurrency: the session store and the login limiter have no locks. They are correct only
because these routes are `async def` (never run in the thread pool) on the one event loop, and
in `login` nothing awaits from the limiter check to the response (steps 3 to 5 are plain code).
tests/unit/test_web_concurrency.py checks both: every route is a coroutine function, and no
await, async for or async with comes after `limiter.allowed(` in `login`.

Forbidden: logging or echoing the token, the body, the session id or the CSRF token. Comparing
before checking the limit. Answering a failed login with anything that depends on why.
"""

import hashlib
import hmac
import json

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.requests import ClientDisconnect

from antifaz.api.errors import (
    CsrfRejectedError,
    PanelLoginFailedError,
    PanelUnauthorizedError,
    TooManyAttemptsError,
)
from antifaz.web.cookies import session_cookie
from antifaz.web.csrf import is_json, is_same_origin
from antifaz.web.door import PanelState, panel_context
from antifaz.web.forwarded import client_ip
from antifaz.web.pages import LOGIN_PAGE, panel_page

MAX_LOGIN_BODY = 1024
LOGIN_BODY_SECONDS = 5
MAX_TOKEN_CHARACTERS = 256


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("repeated key")
    return dict(pairs)


def _token(body: bytes | None) -> str | None:
    """The token of a body that is exactly {"token": str} (at most 256 characters), or None."""
    if body is None:
        return None
    try:
        data = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_keys)
    except (ValueError, RecursionError):  # bad UTF-8 and bad JSON are ValueErrors
        return None
    if not isinstance(data, dict) or list(data) != ["token"]:
        return None
    token = data["token"]
    if not isinstance(token, str) or len(token) > MAX_TOKEN_CHARACTERS:
        return None
    return token


async def _read_capped(request: Request) -> bytes | None:
    """The body, or None as soon as it passes MAX_LOGIN_BODY (nothing more is read)."""
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_LOGIN_BODY:
            return None
    return bytes(body)


def _fingerprint(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).digest()


def build_router(state: PanelState) -> APIRouter:
    """The panel's routes over `state`: the only thing they get from the gateway."""
    router = APIRouter(include_in_schema=False)

    @router.get("/panel/login")
    async def login_page() -> HTMLResponse:
        return HTMLResponse(LOGIN_PAGE)

    @router.post("/panel/login")
    async def login(request: Request) -> Response:
        context = panel_context(request.scope)
        headers = request.scope["headers"]
        if not (is_json(headers) and is_same_origin(headers, context.scheme, context.host)):
            raise CsrfRejectedError()
        try:
            with anyio.fail_after(LOGIN_BODY_SECONDS):
                body = await _read_capped(request)
        except (ClientDisconnect, TimeoutError):
            raise PanelLoginFailedError() from None  # not counted: no token was tried
        # From here to the response, no await: the limiter and the store change in one go.
        address = client_ip(request.scope, state.trusted_proxies)
        allowed, wait = state.limiter.allowed(address)
        if not allowed:
            raise TooManyAttemptsError(wait)
        token = _token(body)
        if token is None or not hmac.compare_digest(_fingerprint(token), state.token_fingerprint):
            state.limiter.record_failure(address)
            raise PanelLoginFailedError()
        if context.session_id is not None:
            state.sessions.delete(context.session_id)
        session_id, _ = state.sessions.create()
        return JSONResponse(
            {"ok": True}, headers={"Set-Cookie": session_cookie(context.scheme, session_id)}
        )

    @router.get("/panel")
    async def panel(request: Request) -> HTMLResponse:
        session = panel_context(request.scope).session
        if session is None:  # pragma: no cover - the door never lets it through
            raise PanelUnauthorizedError()
        return HTMLResponse(panel_page(session.csrf_token))

    return router
