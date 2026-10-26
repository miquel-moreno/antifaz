"""«Comprobar ahora» (issue 53, PR 11e-4): signatures only, to be implemented."""

from typing import Any, Literal

import httpx

from antifaz.config import Settings

ANTHROPIC_VERSION = "2023-06-01"
CHECK_SECONDS = 5.0
CHECK_RESULTS: tuple[str, ...] = ()


def model_list_request(
    provider: Literal["openai", "anthropic"], settings: Settings
) -> tuple[str, dict[str, str]]:
    raise NotImplementedError


async def check_providers(
    client: httpx.AsyncClient, settings: Settings, *, seconds: float = CHECK_SECONDS
) -> list[dict[str, Any]]:
    raise NotImplementedError
