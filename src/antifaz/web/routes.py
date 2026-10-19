"""The panel's routes (ADR-0018): getting in (11b-2a) and out (11b-2b). Every one is `async def`.

The door (door.py) has already checked CSRF for every method that is not GET or HEAD: layer 3
and JSON for all of them, and the session's token for all but the login. A route never checks
CSRF again.

- GET /panel/login: the login page (public), rendered from its template once per language, at
  startup. The language (11c-2b): `?lang=xx` if it is a loaded catalog, else Accept-Language,
  else English (web/i18n.py); never the raw value. The answer says it with Content-Language and
  `Vary: Accept-Language`, as GET /panel does.
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
- GET /panel: the panel (templates/panel.html, 11c-2b) in the request's language, with the
  session's CSRF token in a meta.
- POST /panel/logout: the door let it through with a live session and its CSRF token. The
  session is closed in the store (reason "logout"; its subscribers are told before the answer
  leaves), and the answer is {"ok": true} with exactly one Set-Cookie, the expired cookie of the
  request's scheme, and `Clear-Site-Data: "cache"` (never "cookies": on localhost it would wipe
  the cookies of every app on every port). The body is not read. Without a session, the door
  answers 401 and no cookie is touched. Two logouts of the same session at the same time can
  both answer 200 with the expired cookie: the door let both through before either closed it.
  That is idempotent and harmless (the session is closed once, its subscribers told once).
- GET /panel/api/status (11c-3b): the «Estado» view's data, for a live session only (the door:
  the API key never opens it, and it is not public). JSON with the closed schema of
  web/status_response.py, built from PanelState.status (web/status.py): booleans, counts, enums,
  the version and checked hosts, never a key or a base URL. It reads local state only (the NER
  state at that moment and the uptime) and never calls a provider. No language: no Vary or
  Content-Language. Only GET: POST and the others are 405 once the door let them through, HEAD
  is 405 too.
- POST /panel/api/try (11d-a): «Prueba un texto», for a live session with its CSRF token (the
  door). In this order:
  1. The rate limit (web/try_limit.py), keyed by the session's fingerprint: at most 30 tries
     in 60 s, else 429 too_many_tries with Retry-After. Taken BEFORE anything awaits or the body
     is read: a refused try costs nothing. Every try counts, good or bad.
  2. The body: a declared Content-Length over MAX_BODY_BYTES (128 KiB) is 413 at once; else it
     is read as a stream and cut as soon as it passes that cap (413), with or without
     Content-Length, within TRY_BODY_SECONDS (else 408 body_timeout). Never parsed as a form.
  3. Exactly {"text": str, "lang": a loaded catalog code} (web/try_text.parse_body), else 400
     invalid_try_body (never quoting it); a text over 20 480 UTF-8 bytes is 413 text_too_large.
  4. PanelState.try_runner (the gateway's: mask -> guard -> simulated reply -> restore, in a
     worker thread, no AI) and the closed schema of web/try_response.py, with the NER state at
     that moment. Any error there is a fixed 500 and one log line with the error's type only:
     an exception's message could hold the text. Busy is a fixed 503 with Retry-After: 1 and
     never a queue: `try_busy` (another Try is running) or `ner_busy` (the NER would make a
     proxy request wait, has no free loaded worker, or is in the Try-only cooldown). A text that
     needs more NER windows than a Try may use is 413 text_too_long_for_ner.
     No response_model: the answer is validated once, here, and FastAPI never re-validates it.
  The answer is JSON with the door's headers and no-store; no Vary or Content-Language. The
  values come back only in it (the scoped exception to invariant 15); the original text never.
  When a session closes, its entry in the rate limit goes too (SessionStore.on_close).

Concurrency: the session store and the login limiter have no locks. They are correct only
because these routes are `async def` (never run in the thread pool) on the one event loop, and
in `login` nothing awaits from the limiter check to the response (steps 3 to 5 are plain code);
`logout` never awaits; `try_a_text` takes its try before its first await.
tests/unit/test_web_concurrency.py checks it: every route is a coroutine function, no await,
async for or async with comes after `limiter.allowed(` in `login`, `logout` has none at all, and
none comes before `.take(` in `try_a_text`.

Forbidden: logging or echoing the token, the body, the session id or the CSRF token.
Logging, echoing in an error or writing anywhere the text or a value of «Prueba un texto». Comparing
before checking the limit. Answering a failed login with anything that depends on why.
"""

import hashlib
import hmac
import json
import logging

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.requests import ClientDisconnect

from antifaz import __version__
from antifaz.http_errors import (
    AppError,
    InvalidTryBodyError,
    PanelInternalError,
    PanelLoginFailedError,
    PanelNotFoundError,
    PanelUnauthorizedError,
    TextTooLargeError,
    TextTooLongForNerError,
    TooManyAttemptsError,
    TooManyTriesError,
    TryBodyTimeoutError,
    TryBusyError,
)
from antifaz.web.cookies import expired_cookie, session_cookie, session_scheme
from antifaz.web.door import PanelState, panel_context
from antifaz.web.forwarded import client_ip
from antifaz.web.sessions import CloseReason
from antifaz.web.status import ner_state, snapshot
from antifaz.web.status_response import StatusResponse
from antifaz.web.templating import Pages
from antifaz.web.try_response import TryResponse
from antifaz.web.try_text import (
    BODY_SECONDS,
    MAX_BODY_BYTES,
    TextTooLongError,
    TryBodyError,
    parse_body,
)

MAX_LOGIN_BODY = 1024
LOGIN_BODY_SECONDS = 5
MAX_TOKEN_CHARACTERS = 256
TRY_BODY_SECONDS = BODY_SECONDS

logger = logging.getLogger("antifaz.panel")


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


async def _read_capped(request: Request, limit: int = MAX_LOGIN_BODY) -> bytes | None:
    """The body, or None as soon as it passes `limit` (nothing more is read)."""
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            return None
    return bytes(body)


async def _try_body(request: Request) -> bytes:
    """The Try body: 413 over MAX_BODY_BYTES (declared or streamed), 408 past the deadline."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise TextTooLargeError()
    raw: bytes | None = None
    problem: type[Exception] | None = None
    try:
        with anyio.fail_after(TRY_BODY_SECONDS):
            raw = await _read_capped(request, MAX_BODY_BYTES)
    except TimeoutError:
        problem = TryBodyTimeoutError
    except ClientDisconnect:
        problem = InvalidTryBodyError  # nobody is listening any more; never a 500
    if problem is not None:
        raise problem()
    if raw is None:
        raise TextTooLargeError()
    return raw


def _try_input(raw: bytes, languages: tuple[str, ...]) -> tuple[str, str]:
    """(text, lang), or the fixed 400 or 413 (raised outside the except block)."""
    parsed: tuple[str, str] | None = None
    too_long = False
    try:
        parsed = parse_body(raw, languages)
    except TextTooLongError:
        too_long = True
    except TryBodyError:
        parsed = None
    if too_long:
        raise TextTooLargeError()
    if parsed is None:
        raise InvalidTryBodyError()
    return parsed


def _fingerprint(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).digest()


def _language(request: Request, pages: Pages) -> str:
    """One of the loaded languages for this request (never the raw ?lang or header)."""
    accept = ", ".join(request.headers.getlist("accept-language"))
    return pages.catalogs.negotiate(request.query_params.get("lang"), accept)


def _page(body: str, lang: str) -> HTMLResponse:
    return HTMLResponse(body, headers={"Content-Language": lang, "Vary": "Accept-Language"})


def build_router(state: PanelState) -> APIRouter:
    """The panel's routes over `state`: the only thing they get from the gateway. The pages are
    rendered from the templates and catalogs here, once (a broken one stops the startup)."""
    router = APIRouter(include_in_schema=False)
    pages = Pages(state.assets, version=__version__)

    def forget_tries(key: str, _reason: CloseReason) -> None:
        state.try_limiter.forget(key)  # a closed session leaves nothing in the rate limit

    state.sessions.on_close(forget_tries)

    @router.get("/panel/login")
    async def login_page(request: Request) -> HTMLResponse:
        lang = _language(request, pages)
        return _page(pages.login(lang), lang)

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
        lang = _language(request, pages)
        return _page(pages.panel(lang, session.csrf_token), lang)

    @router.get("/panel/api/status")
    async def status(request: Request) -> StatusResponse:
        context = panel_context(request.scope)
        if context.session is None:  # pragma: no cover - the door never lets it through
            raise PanelUnauthorizedError()
        return StatusResponse.model_validate(snapshot(state.status, context.scheme))

    # response_model=None: the answer is validated once, in the route's try block below; FastAPI
    # must not validate or serialise it again (a failure there would bypass the fixed 500).
    @router.post("/panel/api/try", response_model=None)
    async def try_a_text(request: Request) -> Response:
        context = panel_context(request.scope)
        if context.session is None:  # pragma: no cover - the door never lets it through
            raise PanelUnauthorizedError()
        # Before anything awaits: the check and the count are one plain call.
        wait = state.try_limiter.take(context.session.fingerprint)
        if wait:
            raise TooManyTriesError(wait)
        text, lang = _try_input(await _try_body(request), pages.languages)
        answer: bytes | None = None
        refused: AppError | None = None
        failure = ""
        try:
            result = await state.try_runner(text, lang)
            ner = ner_state(state.status.ner_state())
            answer = TryResponse.model_validate({**result, "ner": ner}).model_dump_json().encode()
        except (TryBusyError, TextTooLongForNerError) as error:
            refused = error  # a fixed 503 (Retry-After, never queued) or 413: nothing of the text
        except Exception as error:
            failure = type(error).__name__  # only the type: the message could hold the text
        if refused is not None:
            raise refused
        if answer is None:
            logger.error("panel try failed: %s", failure)
            raise PanelInternalError()
        return Response(answer, media_type="application/json")

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
