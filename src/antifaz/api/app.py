"""FastAPI application factory."""

from fastapi import FastAPI

from antifaz import __version__
from antifaz.api import health
from antifaz.api.errors import register_error_handlers
from antifaz.api.middleware import request_id_middleware
from antifaz.config import get_settings
from antifaz.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name, version=__version__)
    app.middleware("http")(request_id_middleware)
    register_error_handlers(app)
    app.include_router(health.router)
    return app


app = create_app()
