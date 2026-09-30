"""POST /v1/chat/completions: OpenAI-compatible proxy without streaming (issue 5a, ADR-0013).

auth -> read body (size limit) -> parse -> mask (one call) -> serialize once -> guard.check on
those bytes -> send with the provider key from settings -> restore the known fields.

Forbidden: logging bodies, headers or keys. Forwarding the client's key or headers. Taking the
destination from the client.
"""

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from antifaz.api.errors import NotConfiguredError
from antifaz.api.proxy import (
    answer_json,
    authorize,
    passthrough,
    read_json,
    refuse_streaming,
    send_masked,
)
from antifaz.config import Settings
from antifaz.providers.openai_chat import mask_request, restore_response

router = APIRouter(tags=["openai"])


@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    state = request.app.state
    settings: Settings = state.settings
    if settings.antifaz_api_key is None or settings.openai_api_key is None:
        raise NotConfiguredError()
    gateway_key = settings.antifaz_api_key.get_secret_value()
    authorize([(request.headers.get("authorization", ""), f"Bearer {gateway_key}")])
    body = await read_json(request, settings.max_body_bytes)
    refuse_streaming(body)

    masked, vault = mask_request(body, policy=state.policy, detector=state.detector)
    url = settings.openai_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.openai_api_key.get_secret_value()}"}
    upstream = await send_masked(state.http_client, url, headers, masked, vault)
    if upstream.status_code >= 400:
        return passthrough(upstream)
    return JSONResponse(
        restore_response(answer_json(upstream), vault), status_code=upstream.status_code
    )
