"""POST /v1/chat/completions: OpenAI-compatible proxy without streaming (issue 5a, ADR-0013).

auth -> read body (size limit) -> parse -> mask (one call) -> serialize once -> guard.check on
those bytes -> send with the provider key from settings -> restore the known fields.

Forbidden: logging bodies, headers or keys. Forwarding the client's key or headers. Taking the
destination from the client.
"""

import hmac
import json
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from antifaz import guard
from antifaz.api.errors import (
    BadUpstreamResponseError,
    InvalidRequestError,
    NotConfiguredError,
    PayloadTooLargeError,
    StreamingNotSupportedError,
    UnauthorizedError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from antifaz.config import Settings
from antifaz.providers.openai_chat import mask_request, restore_response

router = APIRouter(tags=["openai"])


def _authorize(request: Request, settings: Settings) -> None:
    if settings.antifaz_api_key is None or settings.openai_api_key is None:
        raise NotConfiguredError()
    expected = f"Bearer {settings.antifaz_api_key.get_secret_value()}".encode()
    given = request.headers.get("authorization", "").encode()
    if not hmac.compare_digest(given, expected):
        raise UnauthorizedError()


async def _read_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise PayloadTooLargeError()
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise PayloadTooLargeError()
        chunks.append(chunk)
    return b"".join(chunks)


def _parse(raw: bytes) -> dict[str, Any]:
    parsed: object = None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        parsed = None
    # Raised outside the except block: the decode error (which may quote the body) is dropped.
    if not isinstance(parsed, dict):
        raise InvalidRequestError() from None
    return parsed


async def _send(client: httpx.AsyncClient, settings: Settings, payload: bytes) -> httpx.Response:
    if settings.openai_api_key is None:  # pragma: no cover - checked in _authorize
        raise NotConfiguredError()
    url = settings.openai_base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key.get_secret_value()}",
        "Content-Type": "application/json",
    }
    try:
        return await client.post(url, content=payload, headers=headers)
    except httpx.TimeoutException:
        timed_out = True
    except httpx.HTTPError:
        timed_out = False
    # Outside the except block: the httpx error (with the URL and maybe more) is not chained.
    raise (UpstreamTimeoutError() if timed_out else UpstreamUnavailableError()) from None


@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    state = request.app.state
    settings: Settings = state.settings
    _authorize(request, settings)
    body = _parse(await _read_body(request, settings.max_body_bytes))
    if body.get("stream") is True:
        raise StreamingNotSupportedError()

    masked, vault = mask_request(body, policy=state.policy, detector=state.detector)
    payload = json.dumps(masked, ensure_ascii=False).encode("utf-8")
    guard.check(payload, vault)  # the same bytes that are sent, right before sending
    upstream = await _send(state.http_client, settings, payload)

    if upstream.status_code >= 400:
        # The provider only saw masked text: its error is returned as it is (not restored).
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )
    answer: object = None
    try:
        answer = upstream.json()
    except (UnicodeDecodeError, ValueError):
        answer = None
    if not isinstance(answer, dict):
        raise BadUpstreamResponseError() from None
    return JSONResponse(restore_response(answer, vault), status_code=upstream.status_code)
