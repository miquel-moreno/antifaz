"""POST /v1/chat/completions: OpenAI-compatible proxy, with streaming (issue 5, ADR-0013).

(key, Origin and Content-Type already checked in api/gate.py) -> read body (size limit) ->
parse (no repeated keys) -> mask (one call) -> serialize once -> guard.check on those bytes ->
send with the provider key from settings -> restore the known fields, whole or streamed
(`stream: true`, part 5c, api/streaming.py).

Forbidden: logging bodies, headers or keys. Forwarding the client's key or headers. Taking the
destination from the client.
"""

from functools import partial
from typing import Any

import anyio.to_thread
from fastapi import APIRouter, Request, Response

from antifaz.api.errors import (
    PROXY_ERRORS,
    InvalidStreamOptionsError,
    NotConfiguredError,
    json_body,
)
from antifaz.api.proxy import (
    answer_json,
    configured_keys,
    is_event_stream,
    passthrough,
    read_json,
    restored_json,
    send_masked,
    stream_requested,
)
from antifaz.api.streaming import stream_response
from antifaz.config import Settings
from antifaz.providers.openai_chat import mask_request, restore_response
from antifaz.providers.openai_stream import OpenAIChatStream

router = APIRouter(tags=["openai"])

# `stream_options` is a setting: an allowlist of keys, booleans only.
STREAM_OPTIONS = frozenset({"include_usage", "include_obfuscation"})


def _check_stream_options(body: dict[str, Any]) -> None:
    options = body.get("stream_options")
    if options is None:
        return
    if not isinstance(options, dict) or not all(
        name in STREAM_OPTIONS and isinstance(value, bool) for name, value in options.items()
    ):
        raise InvalidStreamOptionsError()


@router.post(
    "/v1/chat/completions",
    responses=PROXY_ERRORS,
    openapi_extra=json_body("An OpenAI Chat Completions request"),
)
async def chat_completions(request: Request) -> Response:
    """OpenAI Chat Completions with the personal data masked, and the answer restored."""
    state = request.app.state
    settings: Settings = state.settings
    if settings.openai_api_key is None:
        raise NotConfiguredError()
    body = await read_json(request, settings.max_body_bytes)
    stream = stream_requested(body)
    _check_stream_options(body)

    # In a worker thread: detection (patterns and the NER pool) never blocks the event loop.
    masked, vault = await anyio.to_thread.run_sync(
        partial(mask_request, body, policy=state.policy, detector=state.detector),
        limiter=state.mask_limiter,
    )
    url = settings.openai_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.openai_api_key.get_secret_value()}"}
    keys = configured_keys(settings)
    upstream = await send_masked(
        state.http_client, url, headers, masked, vault, keys, stream=stream
    )
    if upstream.status_code >= 400:
        return passthrough(upstream)
    if stream and is_event_stream(upstream):
        return stream_response(upstream, OpenAIChatStream(vault), keys, state.stream_counters)
    return restored_json(restore_response(answer_json(upstream), vault), keys, upstream.status_code)
