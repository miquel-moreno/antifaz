"""FastAPI application factory.

The server builds the app with `uvicorn --factory antifaz.api.app:create_app`: nothing is
built at import time, so a refused start (ADR-0015) is never an import side effect.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from starlette.middleware.trustedhost import TrustedHostMiddleware

from antifaz import __version__
from antifaz.api import anthropic, health, openai
from antifaz.api.errors import register_error_handlers
from antifaz.api.gate import GateMiddleware
from antifaz.api.middleware import request_id_middleware
from antifaz.config import Settings, check_safe_to_start, get_settings
from antifaz.detect.scan import scan
from antifaz.logging import configure_logging
from antifaz.mask import Detector
from antifaz.policy import DEFAULT_POLICY, Policy
from antifaz.providers.http import build_client


def registered_paths(app: FastAPI) -> frozenset[str]:
    """The paths of every route. `app.routes` holds included routers, not their routes."""
    return frozenset(context.path for context in iter_route_contexts(app.routes) if context.path)


def create_app(
    settings: Settings | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
    detector: Detector = scan,
    policy: Policy = DEFAULT_POLICY,
) -> FastAPI:
    """Build the app. Tests inject `http_client` (httpx.MockTransport) and `detector`.

    Raises UnsafeConfigError (without any key in the message) if the gateway would start open.
    """
    settings = settings or get_settings()
    check_safe_to_start(settings)
    configure_logging(settings.log_level)
    api_key = settings.antifaz_api_key.get_secret_value() if settings.antifaz_api_key else ""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = http_client or build_client(settings)
        app.state.http_client = client
        try:
            yield
        finally:
            if http_client is None:  # an injected client belongs to whoever created it
                await client.aclose()

    # No /docs, /redoc or /openapi.json (fewer public surfaces) and no slash redirects: an
    # alias such as "/v1/messages/" is a 404, never a 307 to a URL built from the Host header.
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    app.state.settings = settings
    app.state.detector = detector
    app.state.policy = policy
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(openai.router)
    app.include_router(anthropic.router)
    app.state.route_paths = registered_paths(app)
    # The last one added runs first: request id -> trusted host -> gate (key, Origin, JSON).
    app.add_middleware(GateMiddleware, api_key=api_key, allowed_origins=settings.allowed_origins)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts, www_redirect=False
    )
    app.middleware("http")(request_id_middleware)
    return app
