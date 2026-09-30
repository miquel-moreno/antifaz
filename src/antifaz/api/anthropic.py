"""POST /v1/messages and /v1/messages/count_tokens: Anthropic proxy without streaming (5b).

Same steps as the OpenAI route (ADR-0013). The Antifaz key comes in `x-api-key` (what Anthropic
clients such as Claude Code send) or in `Authorization: Bearer`. Only `anthropic-version` and
`anthropic-beta` are forwarded, after checking their characters; the provider key comes from
settings.

Forbidden: logging bodies, headers or keys. Forwarding the client's key or other headers.
Changing a reasoning block (invariant 9).
"""

import re
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from antifaz.api.errors import InvalidHeaderError, NotConfiguredError
from antifaz.api.proxy import (
    answer_json,
    authorize,
    passthrough,
    read_json,
    refuse_streaming,
    send_masked,
)
from antifaz.config import Settings
from antifaz.providers.anthropic_messages import mask_request, restore_response
from antifaz.vault import Vault

router = APIRouter(tags=["anthropic"])

FORWARDED_HEADERS = ("anthropic-version", "anthropic-beta")
# A date or beta names separated by commas: no spaces, controls or ";" (no header injection).
_HEADER_VALUE = re.compile(r"[A-Za-z0-9._-]+(?:,[A-Za-z0-9._-]+)*")
_MAX_HEADER_LENGTH = 200


def _headers(request: Request, settings: Settings) -> dict[str, str]:
    if settings.anthropic_api_key is None:  # pragma: no cover - checked in _masked_call
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


async def _masked_call(request: Request, path: str) -> tuple[httpx.Response, Vault]:
    """Auth, body, mask and send to `path` of the configured Anthropic URL."""
    state = request.app.state
    settings: Settings = state.settings
    if settings.antifaz_api_key is None or settings.anthropic_api_key is None:
        raise NotConfiguredError()
    gateway_key = settings.antifaz_api_key.get_secret_value()
    authorize(
        [
            (request.headers.get("x-api-key", ""), gateway_key),
            (request.headers.get("authorization", ""), f"Bearer {gateway_key}"),
        ]
    )
    headers = _headers(request, settings)
    body: dict[str, Any] = await read_json(request, settings.max_body_bytes)
    refuse_streaming(body)  # count_tokens too: one rule for both routes
    masked, vault = mask_request(body, policy=state.policy, detector=state.detector)
    url = settings.anthropic_base_url.rstrip("/") + path
    return await send_masked(state.http_client, url, headers, masked, vault), vault


@router.post("/v1/messages")
async def messages(request: Request) -> Response:
    upstream, vault = await _masked_call(request, "/v1/messages")
    if upstream.status_code >= 400:
        return passthrough(upstream)
    return JSONResponse(
        restore_response(answer_json(upstream), vault), status_code=upstream.status_code
    )


@router.post("/v1/messages/count_tokens")
async def count_tokens(request: Request) -> Response:
    # The answer only has numbers (input_tokens): returned as it is.
    upstream, _ = await _masked_call(request, "/v1/messages/count_tokens")
    return passthrough(upstream)
