"""POST /v1/messages and /v1/messages/count_tokens: Anthropic proxy (5b), with streaming (5c).

Same steps as the OpenAI route (ADR-0013). The Antifaz key (in `x-api-key`, what Anthropic
clients such as Claude Code send, or in `Authorization: Bearer`) is checked in api/gate.py
before any route (ADR-0015). Only `anthropic-version` and
`anthropic-beta` are forwarded, after checking their characters; the provider key comes from
settings. `stream: true` streams the answer restored on the fly (api/streaming.py);
count_tokens never streams.

Forbidden: logging bodies, headers or keys. Forwarding the client's key or other headers.
Changing a reasoning block (invariant 9).
"""

import re
from dataclasses import dataclass
from functools import partial
from typing import Any

import anyio.to_thread
import httpx
from fastapi import APIRouter, Request, Response

from antifaz.api.errors import InvalidHeaderError, NotConfiguredError
from antifaz.api.proxy import (
    answer_json,
    configured_keys,
    is_event_stream,
    passthrough,
    read_json,
    refuse_streaming,
    restored_json,
    send_masked,
    stream_requested,
)
from antifaz.api.streaming import stream_response
from antifaz.config import Settings
from antifaz.providers.anthropic_messages import mask_request, restore_response
from antifaz.providers.anthropic_stream import AnthropicMessagesStream
from antifaz.vault import Vault

router = APIRouter(tags=["anthropic"])

FORWARDED_HEADERS = ("anthropic-version", "anthropic-beta")
# A date or beta names separated by commas: no spaces, controls or ";" (no header injection).
_HEADER_VALUE = re.compile(r"[A-Za-z0-9._-]+(?:,[A-Za-z0-9._-]+)*")
_MAX_HEADER_LENGTH = 200


def _headers(request: Request, settings: Settings) -> dict[str, str]:
    if settings.anthropic_api_key is None:  # pragma: no cover - checked in _prepare
        raise NotConfiguredError()
    headers = {"x-api-key": settings.anthropic_api_key.get_secret_value()}
    for name in FORWARDED_HEADERS:
        value = request.headers.get(name)
        if value is None:
            continue
        if len(value) > _MAX_HEADER_LENGTH or not _HEADER_VALUE.fullmatch(value):
            raise InvalidHeaderError()
        headers[name] = value
    return headers


@dataclass(frozen=True)
class _Call:
    url: str
    headers: dict[str, str]
    masked: dict[str, Any]
    vault: Vault
    keys: list[str]
    stream: bool


async def _prepare(request: Request, path: str, *, can_stream: bool) -> _Call:
    """Headers, body and mask, for `path` of the configured Anthropic URL."""
    state = request.app.state
    settings: Settings = state.settings
    if settings.anthropic_api_key is None:
        raise NotConfiguredError()
    headers = _headers(request, settings)
    body: dict[str, Any] = await read_json(request, settings.max_body_bytes)
    if can_stream:
        stream = stream_requested(body)
    else:
        refuse_streaming(body)  # count_tokens: `stream` checked all the same
        stream = False
    # In a worker thread: detection (patterns and the NER pool) never blocks the event loop.
    masked, vault = await anyio.to_thread.run_sync(
        partial(mask_request, body, policy=state.policy, detector=state.detector),
        limiter=state.mask_limiter,
    )
    url = settings.anthropic_base_url.rstrip("/") + path
    return _Call(url, headers, masked, vault, configured_keys(settings), stream)


async def _send(request: Request, call: _Call) -> httpx.Response:
    client: httpx.AsyncClient = request.app.state.http_client
    return await send_masked(
        client, call.url, call.headers, call.masked, call.vault, call.keys, stream=call.stream
    )


@router.post("/v1/messages")
async def messages(request: Request) -> Response:
    call = await _prepare(request, "/v1/messages", can_stream=True)
    upstream = await _send(request, call)
    if upstream.status_code >= 400:
        return passthrough(upstream)
    if call.stream and is_event_stream(upstream):
        counters = request.app.state.stream_counters
        return stream_response(upstream, AnthropicMessagesStream(call.vault), call.keys, counters)
    return restored_json(
        restore_response(answer_json(upstream), call.vault), call.keys, upstream.status_code
    )


@router.post("/v1/messages/count_tokens")
async def count_tokens(request: Request) -> Response:
    # The answer only has numbers (input_tokens): returned as it is. It never streams.
    call = await _prepare(request, "/v1/messages/count_tokens", can_stream=False)
    return passthrough(await _send(request, call))
