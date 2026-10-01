"""Health check endpoint, used by Docker and uptime monitors.

With the NER on, `ner` says the state of its workers ("ok", "starting", "circuit_open" or
"closed"): never a text, a value or a count of requests.
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from antifaz import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    ner: str | None = None


@router.get("/healthz", response_model_exclude_none=True)
async def healthz(request: Request) -> HealthResponse:
    """Public (no key): the version and, with the NER on, the state of its workers."""
    ner = getattr(request.app.state, "ner", None)
    return HealthResponse(
        status="ok", version=__version__, ner=ner.status() if ner is not None else None
    )
