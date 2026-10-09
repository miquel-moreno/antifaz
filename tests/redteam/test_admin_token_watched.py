"""Invariant 13 for the panel's admin token (issue 53, ADR-0018).

KeyWatch, client_values_hold_a_key and the provider-echo 502 watch ANTIFAZ_ADMIN_TOKEN like the
other keys: no client value may carry it to a provider, and no provider answer may carry it back.
The token is a canary made for these tests only.
"""

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from antifaz.api.proxy import client_values_hold_a_key, configured_keys
from tests.integration.fakes import (
    GATEWAY_KEY,
    PROVIDER_KEY,
    FakeUpstream,
    openai_sse,
    sse_response,
)
from tests.redteam.conftest import (
    ANTHROPIC_AUTH,
    OPENAI_AUTH,
    anthropic_body,
    both_echo,
    gateway_client,
    gateway_settings,
    openai_body,
)

ADMIN_CANARY = "canary-admin-token-not-real-9d1e7b3f5a2c4e60"


def test_configured_keys_include_the_admin_token() -> None:
    keys = configured_keys(gateway_settings(admin_token=SecretStr(ADMIN_CANARY)))

    assert ADMIN_CANARY in keys
    assert GATEWAY_KEY in keys and PROVIDER_KEY in keys


def test_configured_keys_without_a_panel_are_the_same_as_before() -> None:
    assert configured_keys(gateway_settings()) == [GATEWAY_KEY, PROVIDER_KEY, PROVIDER_KEY]


def test_the_admin_token_split_across_client_values_is_caught() -> None:
    keys = configured_keys(gateway_settings(admin_token=SecretStr(ADMIN_CANARY)))

    assert client_values_hold_a_key([ADMIN_CANARY], keys)
    assert client_values_hold_a_key([ADMIN_CANARY[20:], ADMIN_CANARY[:20]], keys)


def test_the_admin_token_in_an_anthropic_header_never_reaches_the_provider() -> None:
    upstream = FakeUpstream(both_echo)
    settings = gateway_settings(admin_token=SecretStr(ADMIN_CANARY))
    for client in gateway_client(upstream, settings):
        for headers in (
            {**ANTHROPIC_AUTH, "anthropic-beta": ADMIN_CANARY},
            {
                **ANTHROPIC_AUTH,
                "anthropic-version": ADMIN_CANARY[16:],
                "anthropic-beta": ADMIN_CANARY[:16],
            },
        ):
            response = client.post("/v1/messages", json=anthropic_body("hola"), headers=headers)

            assert response.status_code == 400
            assert ADMIN_CANARY not in response.text
    assert upstream.requests == []


def test_the_admin_token_in_the_model_list_query_never_reaches_the_provider() -> None:
    upstream = FakeUpstream(both_echo)
    settings = gateway_settings(admin_token=SecretStr(ADMIN_CANARY))
    for client in gateway_client(upstream, settings):
        response = client.get(
            "/v1/models", params={"after_id": ADMIN_CANARY}, headers=ANTHROPIC_AUTH
        )

        assert response.status_code == 400
        assert ADMIN_CANARY not in response.text
    assert upstream.requests == []


def _answer_with_the_token(request: httpx.Request) -> httpx.Response:
    message = {"role": "assistant", "content": f"the token is {ADMIN_CANARY}"}
    return httpx.Response(200, json={"choices": [{"index": 0, "message": message}]})


def _escaped_token(request: httpx.Request) -> httpx.Response:
    escaped = "".join(f"\\u{ord(c):04x}" for c in ADMIN_CANARY)
    body = '{"error": {"message": "bad token ' + escaped + '"}}'
    return httpx.Response(400, content=body.encode(), headers={"content-type": "x"})


@pytest.mark.parametrize("handler", [_answer_with_the_token, _escaped_token])
def test_a_provider_answer_with_the_admin_token_is_dropped_with_502(
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    upstream = FakeUpstream(handler)
    settings = gateway_settings(admin_token=SecretStr(ADMIN_CANARY))
    for client in gateway_client(upstream, settings):
        response = client.post(
            "/v1/chat/completions", json=openai_body("hola"), headers=OPENAI_AUTH
        )

        assert response.status_code == 502
        assert response.json()["error"]["code"] == "bad_upstream_response"
        assert ADMIN_CANARY not in response.text


def test_a_streamed_admin_token_split_between_deltas_never_reaches_the_client() -> None:
    pieces = ["ok ", ADMIN_CANARY[:15], ADMIN_CANARY[15:], " end"]

    def stream(request: httpx.Request) -> httpx.Response:
        return sse_response(openai_sse(pieces))[0]

    upstream = FakeUpstream(stream)
    settings = gateway_settings(admin_token=SecretStr(ADMIN_CANARY))
    for client in gateway_client(upstream, settings):
        response = client.post(
            "/v1/chat/completions", json=openai_body("hola", stream=True), headers=OPENAI_AUTH
        )
        text = response.text

    rebuilt = "".join(
        str(json.loads(line[6:])["choices"][0]["delta"].get("content", ""))
        for line in text.splitlines()
        if line.startswith("data: {") and '"choices"' in line
    )
    assert ADMIN_CANARY not in text
    assert ADMIN_CANARY not in rebuilt
    assert ADMIN_CANARY[:15] in rebuilt  # the part sent before the token completed is not a key
