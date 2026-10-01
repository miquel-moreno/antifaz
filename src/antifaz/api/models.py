"""GET /v1/models: the configured provider's model list (issue 29).

The OpenAI SDK asks for `GET {base}/models` and the Anthropic SDK for `GET /v1/models` with
`anthropic-version`: the same path. That header picks the provider (with it, Anthropic;
without it, OpenAI). The provider key comes from settings, and the client may only add the
checked `anthropic-version` and `anthropic-beta` (Anthropic) and an allowlist of query
parameters, rebuilt from the checked values. No body is sent; the query values pass through
the detector all the same (an id cannot be masked: personal data in one blocks). The answer
is returned as JSON only if no configured key is in it (invariant 13).

Forbidden: forwarding any other client header or query parameter. Taking the destination from
the client. Logging the query.
"""

import re
from functools import partial

import anyio.to_thread
from fastapi import APIRouter, Request, Response

from antifaz.api.anthropic import anthropic_headers
from antifaz.api.errors import InvalidQueryError, NotConfiguredError, error_responses
from antifaz.api.proxy import answer_json, configured_keys, passthrough, restored_json, send_get
from antifaz.config import Settings
from antifaz.errors import UnmaskableField
from antifaz.mask import mask

router = APIRouter(tags=["models"])

# ASCII only ([0-9], not \d): a model id or a cursor. No "/", "?", "&", "#", "@", "%" or spaces.
_ID = re.compile(r"[A-Za-z0-9._:-]{1,200}")
_LIMIT = re.compile(r"[1-9][0-9]{0,3}")
MAX_LIMIT = 1000
# Anthropic's documented pagination parameters. The OpenAI list takes none.
ANTHROPIC_QUERY = frozenset({"after_id", "before_id", "limit"})

_PARAMETERS = [
    {
        "name": "anthropic-version",
        "in": "header",
        "required": False,
        "description": "With it, the Anthropic list; without it, the OpenAI list.",
        "schema": {"type": "string"},
    },
    *(
        {
            "name": name,
            "in": "query",
            "required": False,
            "description": "Anthropic only: letters, digits, . _ : - (up to 200).",
            "schema": {"type": "string", "pattern": _ID.pattern},
        }
        for name in ("after_id", "before_id")
    ),
    {
        "name": "limit",
        "in": "query",
        "required": False,
        "description": "Anthropic only.",
        "schema": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
    },
]


def _valid(name: str, value: str) -> bool:
    if name == "limit":
        return bool(_LIMIT.fullmatch(value)) and int(value) <= MAX_LIMIT
    return bool(_ID.fullmatch(value))


def _query(request: Request, allowed: frozenset[str], keys: list[str]) -> list[tuple[str, str]]:
    """The query as checked pairs: only allowed names, each once, with a valid value."""
    pairs = request.query_params.multi_items()
    names = [name for name, _ in pairs]
    if (
        len(set(names)) != len(names)
        or not all(name in allowed and _valid(name, value) for name, value in pairs)
        or any(key in value for _, value in pairs for key in keys)
    ):
        raise InvalidQueryError()
    return pairs


@router.get(
    "/v1/models",
    responses=error_responses(400, 401, 403, 502, 503, 504),
    openapi_extra={"parameters": _PARAMETERS},
)
async def list_models(request: Request) -> Response:
    """The provider's model list, as the provider sends it. Provider errors pass as they are."""
    state = request.app.state
    settings: Settings = state.settings
    if "anthropic-version" in request.headers:
        headers = anthropic_headers(request, settings)
        url = settings.anthropic_base_url.rstrip("/") + "/v1/models"
        allowed = ANTHROPIC_QUERY
    else:
        if settings.openai_api_key is None:
            raise NotConfiguredError()
        headers = {"Authorization": f"Bearer {settings.openai_api_key.get_secret_value()}"}
        url = settings.openai_base_url.rstrip("/") + "/models"
        allowed = frozenset()
    keys = configured_keys(settings)
    params = _query(request, allowed, keys)
    if params:
        # In a worker thread, as in the proxy routes (the NER may run).
        result = await anyio.to_thread.run_sync(
            partial(mask, [value for _, value in params], state.policy, detector=state.detector),
            limiter=state.mask_limiter,
        )
        if result.hidden:
            raise UnmaskableField()
    upstream = await send_get(state.http_client, url, headers, params, keys)
    if upstream.status_code >= 400:
        return passthrough(upstream)
    # Nothing to restore (no placeholders were sent): checked for keys once more and returned.
    return restored_json(answer_json(upstream), keys, upstream.status_code)
