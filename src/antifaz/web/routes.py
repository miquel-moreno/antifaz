"""The panel's routes (ADR-0018): getting in (11b-2a) and out (11b-2b). Every one is `async def`.

The door (door.py) has already checked CSRF for every method that is not GET or HEAD: layer 3
and JSON for all of them, and the session's token for all but the login. A route never checks
CSRF again.

- GET /panel/login: the login page (public), rendered from its template once, at startup.
- GET /panel/static/{name} (11c-1): a static file of the manifest (assets.py), its bytes from
  memory with its fixed Content-Type. Public only for the exact hashed names (the door's rule);
  any other name reaches this route only with a session, and gets the same 404 as an unknown
  route. The door sets Cache-Control (immutable only for a 200 here).
- POST /panel/login: the admin token as JSON (public; the door checked layer 3 and JSON, and a
  refusal there does not count as a failure). In this order:
  1. (In the door) same origin and JSON, else 403.
  2. The body, read as a stream and never past 1 KiB, with or without Content-Length, within
     LOGIN_BODY_SECONDS (5 s). A client that disconnects or is too slow gets the same fixed
     401 and it does NOT count: nobody's token was tried, and a slow line must not lock the
     administrator out.
  3. The attempt limit for the client's address (forwarded.client_ip), BEFORE comparing:
     blocked is 429 with Retry-After, even with the right token, and nothing is compared.
  4. Exactly {"token": "<at most 256 characters>"} (no repeated keys), and its SHA-256
     compared with the token's fingerprint by hmac.compare_digest. Anything wrong (body, size,
     token) is one byte-identical 401, and it counts.
  5. A session cookie that came in is closed (reason "replaced": no session fixation), a new
     session bound to the request's scheme is made and the cookie of that scheme is set.
- GET /panel: a placeholder for the interface of 11c-2 (templates/panel.html), with the
  session's CSRF token in a meta.
- POST /panel/logout: the door let it through with a live session and its CSRF token. The
  session is closed in the store (reason "logout"; its subscribers are told before the answer
  leaves), and the answer is {"ok": true} with exactly one Set-Cookie, the expired cookie of the
  request's scheme, and `Clear-Site-Data: "cache"` (never "cookies": on localhost it would wipe
  the cookies of every app on every port). The body is not read. Without a session, the door
  answers 401 and no cookie is touched. Two logouts of the same session at the same time can
  both answer 200 with the expired cookie: the door let both through before either closed it.
  That is idempotent and harmless (the session is closed once, its subscribers told once).

Concurrency: the session store and the login limiter have no locks. They are correct only
because these routes are `async def` (never run in the thread pool) on the one event loop, and
in `login` nothing awaits from the limiter check to the response (steps 3 to 5 are plain code);
`logout` never awaits. tests/unit/test_web_concurrency.py checks it: every route is a coroutine
function, no await, async for or async with comes after `limiter.allowed(` in `login`, and
`logout` has none at all.

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

from antifaz.http_errors import (
    PanelLoginFailedError,
    PanelNotFoundError,
    PanelUnauthorizedError,
    TooManyAttemptsError,
)
from antifaz.web.cookies import expired_cookie, session_cookie, session_scheme
from antifaz.web.door import PanelState, panel_context
from antifaz.web.forwarded import client_ip
from antifaz.web.templating import Pages

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
    """The panel's routes over `state`: the only thing they get from the gateway. The pages are
    rendered from the templates here, once (a broken template stops the startup)."""
    router = APIRouter(include_in_schema=False)
    pages = Pages(state.assets)

    @router.get("/panel/login")
    async def login_page() -> HTMLResponse:
        return HTMLResponse(pages.login)

    @router.get("/panel/static/{name}")
    async def static_file(name: str) -> Response:
        # An exact lookup in memory: the name is never joined to a folder.
        asset = state.assets.get(name)
        if asset is None:
            raise PanelNotFoundError()
        return Response(asset.body, headers={"Content-Type": asset.content_type})

    @router.post("/panel/login")
    async def login(request: Request) -> Response:
        context = panel_context(request.scope)
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
            state.sessions.delete(context.session_id, "replaced")
        session_id, _ = state.sessions.create(session_scheme(context.scheme))
        return JSONResponse(
            {"ok": True}, headers={"Set-Cookie": session_cookie(context.scheme, session_id)}
        )

    @router.get("/panel")
    async def panel(request: Request) -> HTMLResponse:
        session = panel_context(request.scope).session
        if session is None:  # pragma: no cover - the door never lets it through
            raise PanelUnauthorizedError()
        return HTMLResponse(pages.panel(session.csrf_token))

    @router.post("/panel/logout")
    async def logout(request: Request) -> Response:
        context = panel_context(request.scope)
        if context.session is None or context.session_id is None:  # pragma: no cover - the door
            raise PanelUnauthorizedError()
        state.sessions.delete(context.session_id, "logout")
        return JSONResponse(
            {"ok": True},
            headers={
                "Set-Cookie": expired_cookie(context.scheme),
                "Clear-Site-Data": '"cache"',
            },
        )

    return router
