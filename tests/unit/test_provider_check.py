"""«Comprobar ahora» in the gateway (issue 53, PR 11e-4, ADR-0018): api/provider_check.py.

- `model_list_request(provider, settings)`: the one builder of the model-list URL and headers,
  used by GET /v1/models, `antifaz doctor --providers` and the panel's check. A trailing "/" and
  a custom base give the same URL everywhere.
- `check_providers(client, settings)`: both providers at once, each under a total deadline, on
  the client it is given (the gateway's own); only the status code is read and the body never
  (a body that never ends does not hold it). Each answer becomes one value of a closed list,
  plus the HTTP code only for refused, redirect and unexpected. No key: no call. A base URL with
  a user or password: no call. Exceptions are never kept, returned or logged.

Every test runs with the socket guard (tests/conftest.py forbid_network): a connect to anything
but loopback fails, so no real provider can ever be called. The providers are MockTransports.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from antifaz.api import provider_check
from antifaz.api.provider_check import (
    CHECK_RESULTS,
    CHECK_SECONDS,
    check_providers,
    model_list_request,
)
from antifaz.cli import doctor
from antifaz.config import Settings
from tests.conftest import forbid_network
from tests.redteam.conftest import gateway_settings

OPENAI_KEY = "sk-proj-QzCheckOpenaiWvXkMtRpLsYjGhNqZuVoPi"
ANTHROPIC_KEY = "sk-ant-api03-QzCheckAnthropicWvXkMtRpLsYjGh-Qw"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_network(monkeypatch)


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "openai_api_key": SecretStr(OPENAI_KEY),
        "anthropic_api_key": SecretStr(ANTHROPIC_KEY),
        "openai_base_url": "https://openai.check.invalid/v1",
        "anthropic_base_url": "https://anthropic.check.invalid",
    }
    values.update(overrides)
    return gateway_settings(**values)


class Recorder:
    def __init__(self, handler: Callable[[httpx.Request], Any]) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.handler(request)
        if asyncio.iscoroutine(answer):
            answer = await answer
        return answer  # type: ignore[no-any-return]


def _client(handler: Callable[[httpx.Request], Any]) -> tuple[httpx.AsyncClient, Recorder]:
    recorder = Recorder(handler)
    return httpx.AsyncClient(transport=httpx.MockTransport(recorder)), recorder


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": []})


def _by_id(outcomes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {outcome["id"]: outcome for outcome in outcomes}


# --- the one URL builder ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("openai_base", "anthropic_base", "openai_url", "anthropic_url"),
    [
        (
            "https://api.openai.com/v1",
            "https://api.anthropic.com",
            "https://api.openai.com/v1/models",
            "https://api.anthropic.com/v1/models",
        ),
        (
            "https://proxy.check.invalid/openai/v1/",
            "https://proxy.check.invalid:8443/anthropic/",
            "https://proxy.check.invalid/openai/v1/models",
            "https://proxy.check.invalid:8443/anthropic/v1/models",
        ),
    ],
)
def test_the_model_list_request_of_each_provider(
    openai_base: str, anthropic_base: str, openai_url: str, anthropic_url: str
) -> None:
    settings = _settings(openai_base_url=openai_base, anthropic_base_url=anthropic_base)

    assert model_list_request("openai", settings) == (
        openai_url,
        {"Authorization": f"Bearer {OPENAI_KEY}"},
    )
    assert model_list_request("anthropic", settings) == (
        anthropic_url,
        {"x-api-key": ANTHROPIC_KEY, "anthropic-version": provider_check.ANTHROPIC_VERSION},
    )


def test_without_a_key_there_is_no_request_to_build() -> None:
    settings = _settings(openai_api_key=None)

    with pytest.raises(ValueError, match="no key"):
        model_list_request("openai", settings)


def test_doctor_uses_the_same_builder_and_version() -> None:
    assert doctor.ANTHROPIC_VERSION == provider_check.ANTHROPIC_VERSION
    assert doctor.model_list_request is model_list_request


@pytest.mark.parametrize("base", ["https://proxy.check.invalid/v1/", "https://x.check.invalid"])
async def test_v1_models_sends_the_builders_url_and_headers(base: str) -> None:
    """GET /v1/models (OpenAI) reaches exactly the URL and key the builder gives."""
    from fastapi.testclient import TestClient

    from antifaz.api.app import create_app
    from tests.integration.fakes import GATEWAY_KEY

    settings = _settings(openai_base_url=base, anthropic_base_url=base)
    client, recorder = _client(_ok)
    app = create_app(settings, http_client=client)
    with TestClient(app) as gateway:
        assert gateway.get(
            "/v1/models", headers={"Authorization": f"Bearer {GATEWAY_KEY}"}
        ).is_success
        headers = {"x-api-key": GATEWAY_KEY, "anthropic-version": "2023-06-01"}
        assert gateway.get("/v1/models", headers=headers).is_success
    openai, anthropic = recorder.requests
    url, sent = model_list_request("openai", settings)
    assert str(openai.url) == url
    assert openai.headers["authorization"] == sent["Authorization"]
    url, sent = model_list_request("anthropic", settings)
    assert str(anthropic.url) == url
    assert anthropic.headers["x-api-key"] == sent["x-api-key"]


# --- the check itself ------------------------------------------------------------------------


async def test_both_answer_200_and_the_key_works() -> None:
    client, recorder = _client(_ok)

    outcomes = await check_providers(client, _settings())

    assert outcomes == [
        {"id": "openai", "result": "ok", "http_status": None},
        {"id": "anthropic", "result": "ok", "http_status": None},
    ]
    urls = sorted(str(request.url) for request in recorder.requests)
    assert urls == [
        "https://anthropic.check.invalid/v1/models",
        "https://openai.check.invalid/v1/models",
    ]
    assert all(request.method == "GET" and request.content == b"" for request in recorder.requests)


async def test_the_check_sends_the_builders_headers() -> None:
    settings = _settings()
    client, recorder = _client(_ok)

    await check_providers(client, settings)

    for request in recorder.requests:
        provider = "openai" if "openai" in request.url.host else "anthropic"
        url, headers = model_list_request(provider, settings)
        assert str(request.url) == url
        for name, value in headers.items():
            assert request.headers[name] == value


@pytest.mark.parametrize(
    ("status", "result", "code"),
    [
        (200, "ok", None),
        (401, "refused", 401),
        (403, "refused", 403),
        (301, "redirect", 301),
        (302, "redirect", 302),
        (307, "redirect", 307),
        (308, "redirect", 308),
        (404, "unexpected", 404),
        (429, "unexpected", 429),
        (500, "unexpected", 500),
        (204, "unexpected", 204),
    ],
)
async def test_each_status_maps_to_one_value(status: int, result: str, code: int | None) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        headers = {"Location": "https://elsewhere.check.invalid/"} if 300 <= status < 400 else {}
        return httpx.Response(status, headers=headers, content=b"body")

    client, recorder = _client(answer)

    outcomes = _by_id(await check_providers(client, _settings()))

    for provider in ("openai", "anthropic"):
        assert outcomes[provider] == {"id": provider, "result": result, "http_status": code}
    assert len(recorder.requests) == 2  # a redirect is never followed


async def test_without_a_key_that_provider_is_not_called() -> None:
    client, recorder = _client(_ok)

    outcomes = _by_id(await check_providers(client, _settings(anthropic_api_key=None)))

    assert outcomes["anthropic"] == {"id": "anthropic", "result": "no_key", "http_status": None}
    assert outcomes["openai"]["result"] == "ok"
    assert [request.url.host for request in recorder.requests] == ["openai.check.invalid"]


@pytest.mark.parametrize(
    "base",
    [
        "https://user:pass@proxy.check.invalid/v1",
        "https://user@proxy.check.invalid/v1",
        "https://proxy.check.invalid\\@evil.check.invalid/v1",
    ],
)
async def test_a_base_url_with_a_user_or_password_is_not_called(base: str) -> None:
    client, recorder = _client(_ok)

    outcomes = _by_id(await check_providers(client, _settings(openai_base_url=base)))

    assert outcomes["openai"] == {
        "id": "openai",
        "result": "not_checked_credentials",
        "http_status": None,
    }
    assert [request.url.host for request in recorder.requests] == ["anthropic.check.invalid"]


async def test_a_url_httpx_refuses_is_unreachable() -> None:
    client, recorder = _client(_ok)

    outcomes = _by_id(await check_providers(client, _settings(openai_base_url="https://[v1.abc]/")))

    assert outcomes["openai"]["result"] == "unreachable"
    assert outcomes["openai"]["http_status"] is None
    assert [request.url.host for request in recorder.requests] == ["anthropic.check.invalid"]


@pytest.mark.parametrize(
    "error", [httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError, RuntimeError]
)
async def test_any_other_error_is_unreachable_and_never_kept(
    error: type[Exception], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    def fail(request: httpx.Request) -> httpx.Response:
        raise error(f"boom {OPENAI_KEY}")

    client, _ = _client(fail)

    outcomes = await check_providers(client, _settings())

    assert [outcome["result"] for outcome in outcomes] == ["unreachable", "unreachable"]
    assert OPENAI_KEY not in caplog.text
    assert "boom" not in caplog.text
    assert "boom" not in repr(outcomes)


async def test_a_provider_that_never_answers_times_out_within_the_deadline() -> None:
    async def hang(request: httpx.Request) -> httpx.Response:
        if "anthropic" in request.url.host:
            await asyncio.sleep(3600)
        return httpx.Response(200)

    client, _ = _client(hang)
    loop = asyncio.get_running_loop()
    started = loop.time()

    outcomes = _by_id(await check_providers(client, _settings(), seconds=0.2))

    assert outcomes["anthropic"] == {"id": "anthropic", "result": "timeout", "http_status": None}
    assert outcomes["openai"]["result"] == "ok"
    assert loop.time() - started < 30  # a hang guard, not a speed measure


async def test_an_httpx_timeout_is_a_timeout() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    client, _ = _client(slow)

    outcomes = await check_providers(client, _settings())

    assert [outcome["result"] for outcome in outcomes] == ["timeout", "timeout"]


async def test_both_providers_are_asked_at_the_same_time() -> None:
    both_in = asyncio.Event()
    inside = 0

    async def meet(request: httpx.Request) -> httpx.Response:
        nonlocal inside
        inside += 1
        if inside == 2:
            both_in.set()
        await asyncio.wait_for(both_in.wait(), 10)  # only both at once get past this
        return httpx.Response(200)

    client, _ = _client(meet)

    outcomes = await check_providers(client, _settings(), seconds=20)

    assert [outcome["result"] for outcome in outcomes] == ["ok", "ok"]


async def test_the_body_is_never_read_even_one_that_never_ends() -> None:
    read: list[int] = []

    async def endless() -> AsyncIterator[bytes]:
        while True:
            read.append(1)
            yield b"x" * 1024
            await asyncio.sleep(0)

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=endless())

    client, _ = _client(answer)

    outcomes = await check_providers(client, _settings(), seconds=5)

    assert [outcome["result"] for outcome in outcomes] == ["ok", "ok"]
    assert read == []  # not one chunk was pulled


def test_the_deadline_and_the_closed_list() -> None:
    assert CHECK_SECONDS == 5.0
    assert CHECK_RESULTS == (
        "ok",
        "refused",
        "redirect",
        "unexpected",
        "timeout",
        "unreachable",
        "no_key",
        "not_checked_credentials",
    )


def test_the_check_results_match_the_panel_schema() -> None:
    import typing

    from antifaz.web.status_response import CheckResult

    assert set(typing.get_args(CheckResult)) == set(CHECK_RESULTS)


@pytest.mark.parametrize("status", [600, 999])
async def test_a_status_outside_100_to_599_is_unexpected_without_a_code(status: int) -> None:
    """Review of 11e-4: h11 accepts any three digits; such an answer is unexpected, no code."""
    client, _ = _client(lambda request: httpx.Response(status))

    outcomes = await check_providers(client, _settings())

    assert outcomes == [
        {"id": "openai", "result": "unexpected", "http_status": None},
        {"id": "anthropic", "result": "unexpected", "http_status": None},
    ]


def test_the_schema_takes_unexpected_without_a_code_and_no_other_error_without_one() -> None:
    from pydantic import ValidationError

    from antifaz.web.status_response import ProviderResult

    ProviderResult.model_validate({"id": "openai", "result": "unexpected", "http_status": None})
    for result in ("refused", "redirect"):
        with pytest.raises(ValidationError, match="HTTP code"):
            ProviderResult.model_validate({"id": "openai", "result": result, "http_status": None})
    with pytest.raises(ValidationError):
        ProviderResult.model_validate({"id": "openai", "result": "unexpected", "http_status": 999})
