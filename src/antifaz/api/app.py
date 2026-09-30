"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from antifaz import __version__
from antifaz.api import anthropic, health, openai
from antifaz.api.errors import register_error_handlers
from antifaz.api.middleware import request_id_middleware
from antifaz.config import Settings, get_settings
from antifaz.detect.scan import scan
from antifaz.logging import configure_logging
from antifaz.mask import Detector
from antifaz.policy import DEFAULT_POLICY, Policy
from antifaz.providers.http import build_client


def create_app(
    settings: Settings | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
    detector: Detector = scan,
    policy: Policy = DEFAULT_POLICY,
) -> FastAPI:
    """Build the app. Tests inject `http_client` (httpx.MockTransport) and `detector`."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = http_client or build_client(settings)
        app.state.http_client = client
        try:
            yield
        finally:
            if http_client is None:  # an injected client belongs to whoever created it
                await client.aclose()

    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.detector = detector
    app.state.policy = policy
    app.middleware("http")(request_id_middleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(openai.router)
    app.include_router(anthropic.router)
    return app


app = create_app()
