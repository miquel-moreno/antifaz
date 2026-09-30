"""The HTTP client used to talk to the providers. Tests inject one with httpx.MockTransport."""

import httpx

from antifaz.config import Settings


def build_client(settings: Settings) -> httpx.AsyncClient:
    timeout = httpx.Timeout(
        settings.upstream_timeout_seconds, connect=settings.upstream_connect_timeout_seconds
    )
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False)
