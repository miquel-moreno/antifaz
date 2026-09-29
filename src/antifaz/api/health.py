"""Health check endpoint, used by Docker and uptime monitors."""

from fastapi import APIRouter
from pydantic import BaseModel

from antifaz import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str


@router.get("/healthz")
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)
