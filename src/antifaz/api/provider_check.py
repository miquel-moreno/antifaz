"""«Comprobar ahora»: do the provider keys work? (issue 53, PR 11e-4, ADR-0018).

- `model_list_request(provider, settings)`: the ONE builder of the model-list request of a
  provider: OpenAI `{base}/models` with `Authorization: Bearer`, Anthropic `{base}/v1/models`
  with `x-api-key` and `anthropic-version`, the base without its trailing "/". GET /v1/models
  (api/models.py) takes its URL from here, and so do `antifaz doctor --providers` and the
  panel's check, so the three always reach the same place. Listing models is free.
- `check_providers(client, settings)`: both providers at once (an anyio task group), each under
  a TOTAL deadline of CHECK_SECONDS (5 s: connect, headers and all), on the client it is given.
  The gateway passes its own `app.state.http_client`: no new client, no new way out (no
  redirects, the same proxy settings and the same transport as the proxy routes). Only the
  status code is read; the stream is closed without reading the body (a provider may quote the
  key there). Each provider gives one value of CHECK_RESULTS:
  - no key: `no_key`, and no call;
  - a base URL with a user or password: `not_checked_credentials`, and no call;
  - a URL httpx refuses: `unreachable`;
  - 200 `ok`; 401 or 403 `refused`; 3xx `redirect` (never followed); any other `unexpected`
    (a code outside 100-599, which h11 accepts, is `unexpected` without its code);
  - the deadline or an httpx timeout: `timeout`; any other exception: `unreachable`.
  The HTTP code travels only with refused, redirect and unexpected. An exception is never kept,
  returned or logged (its text could hold the URL or a header).
- `ProviderCheck`: what the panel gets (api/app.py `_panel`), a callable that reads
  `app.state.http_client` at each call. The panel never sees the settings or the client.

Forbidden: building an httpx client here. Following a redirect. Reading the body. Logging
anything but the closed values (the route logs them).
"""

from typing import Any, Literal, cast
from urllib.parse import urlsplit

import anyio
import httpx

from antifaz.config import Settings

ProviderId = Literal["openai", "anthropic"]
PROVIDERS: tuple[ProviderId, ...] = ("openai", "anthropic")
ANTHROPIC_VERSION = "2023-06-01"
CHECK_SECONDS = 5.0
CHECK_RESULTS = (
    "ok",
    "refused",
    "redirect",
    "unexpected",
    "timeout",
    "unreachable",
    "no_key",
    "not_checked_credentials",
)
# The results that carry the provider's HTTP code.
WITH_CODE = frozenset({"refused", "redirect", "unexpected"})


def model_list_request(provider: ProviderId, settings: Settings) -> tuple[str, dict[str, str]]:
    """(url, headers) of the model list of `provider`. ValueError if it has no key."""
    if provider == "openai":
        secret = settings.openai_api_key
        if secret is None:
            raise ValueError("no key")
        url = settings.openai_base_url.rstrip("/") + "/models"
        return url, {"Authorization": f"Bearer {secret.get_secret_value()}"}
    secret = settings.anthropic_api_key
    if secret is None:
        raise ValueError("no key")
    url = settings.anthropic_base_url.rstrip("/") + "/v1/models"
    return url, {"x-api-key": secret.get_secret_value(), "anthropic-version": ANTHROPIC_VERSION}


def _has_credentials(url: str) -> bool | None:
    """Whether the URL has a user or password (httpx's reading, and "@" in the netloc as a
    second opinion); None if httpx refuses it."""
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, ValueError, TypeError):
        return None
    try:
        at_sign = "@" in urlsplit(url).netloc
    except ValueError:
        at_sign = False
    return parsed.userinfo != b"" or at_sign


def _result(status: int) -> str:
    if status == 200:
        return "ok"
    if status in (401, 403):
        return "refused"
    if 300 <= status < 400:
        return "redirect"
    return "unexpected"


async def _check_one(
    client: httpx.AsyncClient, provider: ProviderId, settings: Settings, seconds: float
) -> dict[str, Any]:
    status: int | None = None
    try:
        url, headers = model_list_request(provider, settings)
    except ValueError:
        result = "no_key"
    else:
        credentials = _has_credentials(url)
        if credentials is None:
            result = "unreachable"
        elif credentials:
            result = "not_checked_credentials"
        else:
            result = await _ask(client, url, headers, seconds)
            if result.isdigit():
                status = int(result)
                result = _result(status)
                if not 100 <= status <= 599:  # h11 takes any three digits: no code to show
                    status = None
    return {
        "id": provider,
        "result": result,
        "http_status": status if result in WITH_CODE else None,
    }


async def _ask(client: httpx.AsyncClient, url: str, headers: dict[str, str], seconds: float) -> str:
    """The status code as text, or "timeout" / "unreachable". The body is never read."""
    try:
        with anyio.fail_after(seconds):
            async with client.stream(
                "GET", url, headers=headers, follow_redirects=False
            ) as response:
                return str(response.status_code)  # closed on exit without reading the body
    except (TimeoutError, httpx.TimeoutException):
        return "timeout"
    except Exception:  # never kept: its text could repeat the URL or a header
        return "unreachable"


async def check_providers(
    client: httpx.AsyncClient, settings: Settings, *, seconds: float = CHECK_SECONDS
) -> list[dict[str, Any]]:
    """One closed result per provider (OpenAI first), both asked at the same time."""
    outcomes: dict[ProviderId, dict[str, Any]] = {}

    async def run(provider: ProviderId) -> None:
        outcomes[provider] = await _check_one(client, provider, settings, seconds)

    async with anyio.create_task_group() as group:
        for provider in PROVIDERS:
            group.start_soon(run, provider)
    return [outcomes[provider] for provider in PROVIDERS]


class ProviderCheck:
    """The panel's «Comprobar ahora»: the gateway's own client, read at each call from the
    app's state (it is made in the lifespan). `seconds` is the deadline (tests shorten it)."""

    def __init__(self, state: Any, settings: Settings, seconds: float = CHECK_SECONDS) -> None:
        self._state = state
        self._settings = settings
        self.seconds = seconds

    def __repr__(self) -> str:
        return "ProviderCheck()"

    async def run(self) -> list[dict[str, Any]]:
        client = cast(httpx.AsyncClient, self._state.http_client)
        return await check_providers(client, self._settings, seconds=self.seconds)

    async def __call__(self) -> list[dict[str, Any]]:
        return await self.run()
