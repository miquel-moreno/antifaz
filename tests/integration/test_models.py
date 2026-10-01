"""GET /v1/models against a fake upstream: routing, query allowlist, headers and errors.

The route is shared by the OpenAI SDK (GET {base}/models, no anthropic-version) and the
Anthropic SDK (GET /v1/models with anthropic-version): that header picks the provider.
"""

from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.integration.fakes import (
    GATEWAY_KEY,
    PROVIDER_KEY,
    FakeUpstream,
    models_list,
    raiser,
)
from tests.redteam.conftest import gateway_client, gateway_settings

OPENAI_KEY = {"Authorization": f"Bearer {GATEWAY_KEY}"}
ANTHROPIC_KEY = {"x-api-key": GATEWAY_KEY, "anthropic-version": "2023-06-01"}


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream(models_list)


@pytest.fixture
def client(upstream: FakeUpstream) -> Iterator[TestClient]:
    yield from gateway_client(upstream)


def test_without_anthropic_version_it_lists_the_openai_models(
    client: TestClient, upstream: FakeUpstream
) -> None:
    response = client.get("/v1/models", headers=OPENAI_KEY)

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "gpt-x"
    (sent,) = upstream.requests
    assert sent.method == "GET"
    assert str(sent.url) == "https://upstream.invalid/v1/models"
    assert sent.headers["authorization"] == f"Bearer {PROVIDER_KEY}"
    assert sent.content == b""


def test_with_anthropic_version_it_lists_the_anthropic_models(
    client: TestClient, upstream: FakeUpstream
) -> None:
    response = client.get(
        "/v1/models", headers={**ANTHROPIC_KEY, "anthropic-beta": "beta-1,beta-2"}
    )

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "claude-x"
    (sent,) = upstream.requests
    assert str(sent.url) == "https://anthropic.invalid/v1/models"
    assert sent.headers["x-api-key"] == PROVIDER_KEY
    assert sent.headers["anthropic-version"] == "2023-06-01"
    assert sent.headers["anthropic-beta"] == "beta-1,beta-2"
    assert "authorization" not in sent.headers


def test_the_bearer_key_also_works_for_anthropic(
    client: TestClient, upstream: FakeUpstream
) -> None:
    headers = {**OPENAI_KEY, "anthropic-version": "2023-06-01"}

    assert client.get("/v1/models", headers=headers).status_code == 200
    assert upstream.requests[0].url.host == "anthropic.invalid"


def test_anthropic_pagination_parameters_are_forwarded(
    client: TestClient, upstream: FakeUpstream
) -> None:
    response = client.get(
        "/v1/models",
        params={"limit": "20", "after_id": "claude-sonnet-4-5-20250929"},
        headers=ANTHROPIC_KEY,
    )

    assert response.status_code == 200
    assert upstream.requests[0].url.params.multi_items() == [
        ("limit", "20"),
        ("after_id", "claude-sonnet-4-5-20250929"),
    ]


def test_before_id_is_forwarded_too(client: TestClient, upstream: FakeUpstream) -> None:
    response = client.get("/v1/models", params={"before_id": "claude-x"}, headers=ANTHROPIC_KEY)

    assert response.status_code == 200
    assert upstream.requests[0].url.params["before_id"] == "claude-x"


@pytest.mark.parametrize(
    ("provider", "headers"), [("openai", OPENAI_KEY), ("anthropic", ANTHROPIC_KEY)]
)
def test_a_provider_without_a_key_answers_503(
    upstream: FakeUpstream, provider: str, headers: dict[str, str]
) -> None:
    settings = gateway_settings(**{f"{provider}_api_key": None})
    for client in gateway_client(upstream, settings):
        response = client.get("/v1/models", headers=headers)

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "not_configured"
    assert upstream.requests == []


def test_the_other_provider_still_works_without_the_first_key(upstream: FakeUpstream) -> None:
    for client in gateway_client(upstream, gateway_settings(openai_api_key=None)):
        assert client.get("/v1/models", headers=ANTHROPIC_KEY).status_code == 200


def test_provider_errors_are_returned_as_they_are(upstream: FakeUpstream) -> None:
    error = {"error": {"type": "authentication_error", "message": "invalid x-api-key"}}
    upstream.handler = lambda request: httpx.Response(401, json=error)
    for client in gateway_client(upstream):
        response = client.get("/v1/models", headers=ANTHROPIC_KEY)

        assert response.status_code == 401
        assert response.json() == error


@pytest.mark.parametrize(
    ("handler", "status", "code"),
    [
        (raiser(httpx.ConnectError), 502, "upstream_unavailable"),
        (raiser(httpx.ReadTimeout), 504, "upstream_timeout"),
        (
            lambda r: httpx.Response(302, headers={"Location": "https://x.invalid/"}),
            502,
            "upstream_redirect",
        ),
        (lambda r: httpx.Response(200, content=b"<html>"), 502, "bad_upstream_response"),
        (lambda r: httpx.Response(200, json=[1, 2]), 502, "bad_upstream_response"),
    ],
)
def test_upstream_failures_give_fixed_errors(
    upstream: FakeUpstream,
    handler: Callable[[httpx.Request], httpx.Response],
    status: int,
    code: str,
) -> None:
    upstream.handler = handler
    for client in gateway_client(upstream):
        response = client.get("/v1/models", headers=OPENAI_KEY)

        assert response.status_code == status
        assert response.json()["error"]["code"] == code
