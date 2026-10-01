"""POST /antifaz/scan: where the personal data in a text are, never what they are (issue 29).

Body `{"text": "..."}` and nothing else, read with the proxy's rules (size limit, UTF-8, no
repeated keys). The same detector as the proxy (with the NER when it is on), in the same
thread limiter, with the same checks (`find_spans`): a failing detector blocks with 400.
The answer has only the type and the positions (Python string indexes: Unicode code points)
of each detection. Nothing goes to a provider.

Anyone with the key can probe the detector with it (what it finds and what it misses): the
same as running the library or `antifaz scan`, which the key holder can already do.

Forbidden: returning or logging the text, a value or a substring of either.
"""

from functools import partial

import anyio.to_thread
from fastapi import APIRouter, Request
from pydantic import BaseModel

from antifaz.api.errors import InvalidScanBodyError, error_responses
from antifaz.api.proxy import read_json
from antifaz.config import Settings
from antifaz.detect.types import EntityType
from antifaz.mask import find_spans

router = APIRouter(tags=["antifaz"])


class Entity(BaseModel):
    type: EntityType
    start: int
    end: int


class ScanResponse(BaseModel):
    entities: list[Entity]


_BODY = {
    "required": True,
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            }
        }
    },
}


@router.post(
    "/antifaz/scan",
    responses=error_responses(400, 401, 403, 413, 415),
    openapi_extra={"requestBody": _BODY},
)
async def scan_text(request: Request) -> ScanResponse:
    """Types and positions (`[start, end)`, in Unicode characters) of the personal data found."""
    state = request.app.state
    settings: Settings = state.settings
    body = await read_json(request, settings.max_body_bytes)
    text = body.get("text")
    if body.keys() != {"text"} or not isinstance(text, str):
        raise InvalidScanBodyError()
    (spans,) = await anyio.to_thread.run_sync(
        partial(find_spans, [text], state.policy, detector=state.detector),
        limiter=state.mask_limiter,
    )
    return ScanResponse(
        entities=[Entity(type=span.type, start=span.start, end=span.end) for span in spans]
    )
