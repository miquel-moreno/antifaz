"""POST /v1/messages and /v1/messages/count_tokens against a fake upstream. No real LLM calls."""

import json
import logging
from collections.abc import Callable, Iterator, Sequence

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from antifaz import Span, guard
from antifaz.api.app import create_app
from antifaz.config import Settings
from antifaz.detect.scan import scan
from tests.conftest import SENTINEL_DNI
from tests.integration.fakes import (
    GATEWAY_KEY,
    PROVIDER_KEY,
    FakeUpstream,
    Handler,
    compact,
    misses_repeats,
    raiser,
)

UPSTREAM = "https://anthropic.invalid"
KEY = {"x-api-key": GATEWAY_KEY, "anthropic-version": "2023-06-01"}
SIGNATURE = "EqQBCkYIARgCKkBmaXJtYS1mYWxzYS1kZS1wcnVlYmE+/=="
THINKING = {"type": "thinking", "thinking": "Veo [[ES_DNI_1]] y [[x]]", "signature": SIGNATURE}
REDACTED = {"type": "redacted_thinking", "data": "RW5jcnlwdGVkLWZha2U+/=="}


def _last_text(body: dict[str, object]) -> str:
    content = body["messages"][-1]["content"]  # type: ignore[index]
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content)


def echo(request: httpx.Request) -> httpx.Response:
    """Answers with the last user text, placeholders included, as an assistant message."""
    if request.url.path.endswith("/count_tokens"):
        return httpx.Response(200, json={"input_tokens": 42})
    body = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": body.get("model"),
            "content": [{"type": "text", "text": _last_text(body)}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    )


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "antifaz_api_key": SecretStr(GATEWAY_KEY),
        "anthropic_api_key": SecretStr(PROVIDER_KEY),
        "anthropic_base_url": UPSTREAM,
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _client(
    upstream: FakeUpstream,
    settings: Settings | None = None,
    detector: Callable[[str], Sequence[Span]] = scan,
) -> Iterator[TestClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(settings or _settings(), http_client=http, detector=detector)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream(echo)


@pytest.fixture
def proxy(upstream: FakeUpstream) -> Iterator[TestClient]:
    yield from _client(upstream)


def _msg(content: object) -> dict[str, object]:
    return {
        "model": "claude-x",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": content}],
    }


# --- Auth -------------------------------------------------------------------------------


def test_x_api_key_is_accepted(proxy: TestClient, upstream: FakeUpstream) -> None:
    response = proxy.post("/v1/messages", json=_msg("hola"), headers=KEY)

    assert response.status_code == 200
    assert response.json()["content"][0]["text"] == "hola"


def test_bearer_is_accepted(proxy: TestClient) -> None:
    headers = {"Authorization": f"Bearer {GATEWAY_KEY}"}
    response = proxy.post("/v1/messages", json=_msg("hola"), headers=headers)

    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"x-api-key": "wrong"},
        {"x-api-key": f"{GATEWAY_KEY}x"},
        {"x-api-key": f"Bearer {GATEWAY_KEY}"},
        {"Authorization": GATEWAY_KEY},
        {"Authorization": "Bearer wrong"},
        {"x-api-key": "", "Authorization": "Bearer "},
    ],
)
def test_wrong_or_missing_key_is_rejected(
    proxy: TestClient, upstream: FakeUpstream, headers: dict[str, str]
) -> None:
    for path in ("/v1/messages", "/v1/messages/count_tokens"):
        response = proxy.post(path, json=_msg("hola"), headers=headers)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"
        assert GATEWAY_KEY not in response.text
    assert upstream.requests == []


@pytest.mark.parametrize("missing", ["antifaz_api_key", "anthropic_api_key"])
def test_gateway_without_keys_refuses(upstream: FakeUpstream, missing: str) -> None:
    for client in _client(upstream, _settings(**{missing: None})):
        response = client.post("/v1/messages", json=_msg("hola"), headers=KEY)

        assert response.status_code == 503
    assert upstream.requests == []


# --- Headers and destination --------------------------------------------------------------


def test_only_settings_key_and_anthropic_headers_reach_upstream(
    proxy: TestClient, upstream: FakeUpstream
) -> None:
    headers = {
        **KEY,
        "Authorization": f"Bearer {GATEWAY_KEY}",
        "anthropic-beta": "interleaved-thinking-2025-05-14,prompt-caching-2024-07-31",
        "anthropic-dangerous-direct-browser-access": "true",
        "X-Custom": "cliente",
        "X-Base-URL": "http://evil.invalid",
    }
    response = proxy.post(
        "/v1/messages?base_url=http://evil.invalid", json=_msg("hola"), headers=headers
    )

    assert response.status_code == 200
    (sent,) = upstream.requests
    assert str(sent.url) == f"{UPSTREAM}/v1/messages"
    assert sent.headers["x-api-key"] == PROVIDER_KEY
    assert sent.headers["anthropic-version"] == "2023-06-01"
    assert sent.headers["anthropic-beta"] == headers["anthropic-beta"]
    assert "authorization" not in sent.headers
    assert "x-custom" not in sent.headers
    assert "anthropic-dangerous-direct-browser-access" not in sent.headers
    assert GATEWAY_KEY not in str(sent.headers)
    assert GATEWAY_KEY.encode() not in sent.content


def test_missing_anthropic_version_is_not_invented(
    proxy: TestClient, upstream: FakeUpstream
) -> None:
    proxy.post("/v1/messages", json=_msg("hola"), headers={"x-api-key": GATEWAY_KEY})

    (sent,) = upstream.requests
    assert "anthropic-version" not in sent.headers
    assert "anthropic-beta" not in sent.headers


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("anthropic-version", "2023-06-01\tx"),
        ("anthropic-version", "a" * 300),
        ("anthropic-beta", "beta;x=1"),
        ("anthropic-beta", f"x {SENTINEL_DNI}é"),
        ("anthropic-version", ""),
    ],
)
def test_bad_anthropic_header_values_are_rejected(
    proxy: TestClient, upstream: FakeUpstream, name: str, value: str
) -> None:
    response = proxy.post(
        "/v1/messages",
        json=_msg("hola"),
        headers={"x-api-key": GATEWAY_KEY, name: value.encode("utf-8")},  # type: ignore[dict-item]
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_header"
    assert SENTINEL_DNI not in response.text
    assert upstream.requests == []


# --- Masking, thinking and restore ----------------------------------------------------------


def test_round_trip_restores_text_for_the_client(proxy: TestClient, upstream: FakeUpstream) -> None:
    response = proxy.post("/v1/messages", json=_msg(f"Mi DNI es {SENTINEL_DNI}"), headers=KEY)

    assert response.status_code == 200
    assert response.json()["content"][0]["text"] == f"Mi DNI es {SENTINEL_DNI}"
    (sent,) = upstream.requests
    assert SENTINEL_DNI.encode() not in sent.content
    assert b"[[ES_DNI_1]]" in sent.content


def test_tool_use_input_round_trip(upstream: FakeUpstream) -> None:
    def tool_echo(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        tool_input = body["messages"][0]["content"][0]["input"]
        content = [{"type": "tool_use", "id": "toolu_2", "name": "f", "input": tool_input}]
        return httpx.Response(200, json={"type": "message", "content": content})

    upstream.handler = tool_echo
    tool_input = {"dni": SENTINEL_DNI, "nota": 'con "comillas" y \\ barra', "n": 3}
    block = {"type": "tool_use", "id": "toolu_1", "name": "f", "input": tool_input}
    body = {"model": "m", "max_tokens": 5, "messages": [{"role": "assistant", "content": [block]}]}
    for client in _client(upstream):
        response = client.post("/v1/messages", json=body, headers=KEY)

        assert response.status_code == 200
        assert response.json()["content"][0]["input"] == tool_input
    assert SENTINEL_DNI.encode() not in upstream.requests[0].content


def _raw(block: dict[str, object]) -> bytes:
    return json.dumps(block, ensure_ascii=False).encode("utf-8")


def test_thinking_blocks_reach_upstream_byte_identical(
    proxy: TestClient, upstream: FakeUpstream
) -> None:
    # Invariant 9: not masked, not escaped, signature intact.
    body = {
        "model": "m",
        "max_tokens": 5,
        "thinking": {"type": "enabled", "budget_tokens": 1024},
        "messages": [
            {"role": "user", "content": f"DNI {SENTINEL_DNI}"},
            {"role": "assistant", "content": [THINKING, REDACTED, {"type": "text", "text": "ok"}]},
            {"role": "user", "content": "sigue"},
        ],
    }
    response = proxy.post("/v1/messages", json=body, headers=KEY)

    assert response.status_code == 200
    (sent,) = upstream.requests
    assert _raw(THINKING) in sent.content
    assert _raw(REDACTED) in sent.content
    assert json.loads(sent.content)["messages"][1]["content"][:2] == [THINKING, REDACTED]


def test_thinking_blocks_reach_the_client_byte_identical(upstream: FakeUpstream) -> None:
    def with_thinking(request: httpx.Request) -> httpx.Response:
        content = [THINKING, REDACTED, {"type": "text", "text": "[[ES_DNI_1]]"}]
        return httpx.Response(200, json={"type": "message", "content": content})

    upstream.handler = with_thinking
    for client in _client(upstream):
        response = client.post("/v1/messages", json=_msg(f"{SENTINEL_DNI}"), headers=KEY)

        assert response.status_code == 200
        compact_thinking = json.dumps(THINKING, ensure_ascii=False, separators=(",", ":"))
        assert compact_thinking.encode() in response.content
        content = response.json()["content"]
        assert content[:2] == [THINKING, REDACTED]  # placeholders inside thinking stay
        assert content[2]["text"] == SENTINEL_DNI


def test_thinking_with_a_hidden_value_blocks(proxy: TestClient, upstream: FakeUpstream) -> None:
    thinking = {**THINKING, "thinking": f"el DNI {SENTINEL_DNI}"}
    body = {"model": "m", "messages": [{"role": "assistant", "content": [thinking]}]}
    response = proxy.post("/v1/messages", json=body, headers=KEY)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "antifaz_blocked"
    assert SENTINEL_DNI not in response.text
    assert upstream.requests == []


@pytest.mark.parametrize(
    "block",
    [
        {"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}},
        {"type": "document", "source": {"type": "file", "file_id": "file_1"}},
        {"type": "server_tool_use", "id": "s", "name": "web_search", "input": {}},
    ],
)
def test_attachments_and_unknown_blocks_are_blocked(
    proxy: TestClient, upstream: FakeUpstream, block: dict[str, object]
) -> None:
    content = [{"type": "text", "text": SENTINEL_DNI}, block]
    response = proxy.post("/v1/messages", json=_msg(content), headers=KEY)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "antifaz_blocked"
    assert SENTINEL_DNI not in response.text
    assert upstream.requests == []


def test_streaming_is_refused_for_now(proxy: TestClient, upstream: FakeUpstream) -> None:
    response = proxy.post("/v1/messages", json={**_msg("hola"), "stream": True}, headers=KEY)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "streaming_not_supported"
    assert upstream.requests == []


@pytest.mark.parametrize("value", ["true", 1, None])
def test_stream_must_be_a_boolean(proxy: TestClient, upstream: FakeUpstream, value: object) -> None:
    response = proxy.post("/v1/messages", json={**_msg("hola"), "stream": value}, headers=KEY)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert upstream.requests == []


def test_invalid_json_is_rejected_without_echo(proxy: TestClient, upstream: FakeUpstream) -> None:
    for path in ("/v1/messages", "/v1/messages/count_tokens"):
        response = proxy.post(
            path,
            content=b"{" + SENTINEL_DNI.encode(),
            headers={**KEY, "Content-Type": "application/json"},
        )

        assert response.status_code == 400
        assert SENTINEL_DNI not in response.text
    assert upstream.requests == []


def test_too_large_body_is_rejected(upstream: FakeUpstream) -> None:
    for client in _client(upstream, _settings(max_body_bytes=100)):
        response = client.post("/v1/messages", json=_msg("a" * 200), headers=KEY)

        assert response.status_code == 413
    assert upstream.requests == []


def test_guard_checks_the_exact_bytes_sent(
    proxy: TestClient, upstream: FakeUpstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[bytes | str] = []
    original = guard.check

    def recording(payload: bytes | str, vault: object) -> None:
        seen.append(payload)
        original(payload, vault)  # type: ignore[arg-type]

    monkeypatch.setattr(guard, "check", recording)

    proxy.post("/v1/messages", json=_msg(f"DNI {SENTINEL_DNI}"), headers=KEY)
    proxy.post("/v1/messages/count_tokens", json=_msg(f"DNI {SENTINEL_DNI}"), headers=KEY)

    assert seen == [request.content for request in upstream.requests]
    assert len(seen) == 2


def test_guard_blocks_what_the_masker_missed(upstream: FakeUpstream) -> None:
    for client in _client(upstream, detector=misses_repeats):
        response = client.post(
            "/v1/messages", json=_msg(f"{SENTINEL_DNI} y {SENTINEL_DNI}"), headers=KEY
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "antifaz_blocked"
    assert upstream.requests == []


# --- count_tokens ---------------------------------------------------------------------------


def test_count_tokens_masks_and_returns_upstream_answer(
    proxy: TestClient, upstream: FakeUpstream
) -> None:
    body = {**_msg(f"DNI {SENTINEL_DNI}"), "system": f"cliente {SENTINEL_DNI}"}
    response = proxy.post("/v1/messages/count_tokens", json=body, headers=KEY)

    assert response.status_code == 200
    assert response.json() == {"input_tokens": 42}
    (sent,) = upstream.requests
    assert str(sent.url) == f"{UPSTREAM}/v1/messages/count_tokens"
    assert sent.headers["x-api-key"] == PROVIDER_KEY
    assert SENTINEL_DNI.encode() not in sent.content
    assert b"[[ES_DNI_1]]" in sent.content


def test_count_tokens_blocks_attachments(proxy: TestClient, upstream: FakeUpstream) -> None:
    content = [{"type": "image", "source": {"type": "base64", "data": "AAAA"}}]
    response = proxy.post("/v1/messages/count_tokens", json=_msg(content), headers=KEY)

    assert response.status_code == 400
    assert upstream.requests == []


# --- Upstream errors ----------------------------------------------------------------------


def test_upstream_error_is_passed_through(upstream: FakeUpstream) -> None:
    error = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    upstream.handler = lambda request: httpx.Response(529, json=error)
    for client in _client(upstream):
        for path in ("/v1/messages", "/v1/messages/count_tokens"):
            response = client.post(path, json=_msg("hola"), headers=KEY)

            assert response.status_code == 529
            assert response.json() == error


@pytest.mark.parametrize(
    ("error", "status"),
    [(httpx.ReadTimeout, 504), (httpx.ConnectTimeout, 504), (httpx.ConnectError, 502)],
)
def test_transport_errors_have_fixed_messages(
    upstream: FakeUpstream, error: type[httpx.HTTPError], status: int
) -> None:
    upstream.handler = raiser(error)
    for client in _client(upstream):
        response = client.post("/v1/messages", json=_msg("hola"), headers=KEY)

        assert response.status_code == status
        assert "boom" not in response.text


def test_upstream_redirect_is_a_502(upstream: FakeUpstream) -> None:
    upstream.handler = lambda request: httpx.Response(
        302, headers={"Location": "http://evil.invalid"}
    )
    for client in _client(upstream):
        for path in ("/v1/messages", "/v1/messages/count_tokens"):
            response = client.post(path, json=_msg("hola"), headers=KEY)

            assert response.status_code == 502
            assert response.json()["error"]["code"] == "upstream_redirect"


def test_non_json_upstream_answer_is_a_502(upstream: FakeUpstream) -> None:
    upstream.handler = lambda request: httpx.Response(
        200, content=b"<html>" + SENTINEL_DNI.encode()
    )
    for client in _client(upstream):
        response = client.post("/v1/messages", json=_msg("hola"), headers=KEY)

        assert response.status_code == 502
        assert SENTINEL_DNI not in response.text


# --- Invariant 8 ------------------------------------------------------------------------------


def test_sentinel_never_in_logs_or_error_bodies(
    upstream: FakeUpstream, caplog: pytest.LogCaptureFixture
) -> None:
    for name in ("", "httpx", "httpcore", "http", "uvicorn", "fastapi", "antifaz"):
        caplog.set_level(logging.DEBUG, logger=name or None)
    bodies: list[str] = []

    def answer_with_sentinel(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"type": "text", "text": SENTINEL_DNI}]})

    def error_echoing_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=request.content)

    scenarios: list[tuple[Handler, Callable[[str], Sequence[Span]], str]] = [
        (echo, scan, "/v1/messages"),
        (echo, scan, "/v1/messages/count_tokens"),
        (answer_with_sentinel, scan, "/v1/messages"),
        (error_echoing_request, scan, "/v1/messages"),
        (raiser(httpx.ReadTimeout), scan, "/v1/messages"),
        (echo, misses_repeats, "/v1/messages"),  # guard block
    ]
    for handler, detector, path in scenarios:
        upstream.handler = handler
        for client in _client(upstream, detector=detector):
            body = {**_msg(f"{SENTINEL_DNI} {SENTINEL_DNI}"), "system": SENTINEL_DNI}
            response = client.post(path, json=body, headers=KEY)
            if response.status_code >= 400:
                bodies.append(response.text)

    assert len(bodies) == 3
    for body in bodies:
        assert SENTINEL_DNI not in body
    logs = caplog.text + "".join(str(r.__dict__) for r in caplog.records)
    for secret in (SENTINEL_DNI, GATEWAY_KEY, PROVIDER_KEY):
        assert secret not in logs


# --- Invariant 2 end to end: guard OFF, the upstream still gets no hidden value -----------

VALUES = ["12345678Z", "X1234567L", "ana@example.com", "ES9121000418450200051332"]


def test_invariant_2_without_guard(upstream: FakeUpstream, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guard, "check", lambda payload, vault: None)  # ONLY in this test

    body = {
        "model": "m",
        "max_tokens": 5,
        "system": [{"type": "text", "text": f"Cliente {VALUES[3]}"}],
        "metadata": {"user_id": VALUES[2]},
        "tools": [
            {"name": "f", "description": f"de {VALUES[1]}", "input_schema": {"type": "object"}}
        ],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": f"soy {VALUES[0]}"}]},
            {
                "role": "assistant",
                "content": [
                    THINKING,
                    {
                        "type": "tool_use",
                        "id": "t",
                        "name": "f",
                        "input": {"dni": VALUES[0], "otros": [VALUES[1], {"mail": VALUES[2]}]},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t", "content": f"ok {VALUES[2]}"},
                    {
                        "type": "tool_result",
                        "tool_use_id": "t",
                        "content": [{"type": "text", "text": VALUES[3]}],
                    },
                ],
            },
        ],
        "campo_nuevo": VALUES[1],
    }
    for client in _client(upstream):
        for path in ("/v1/messages", "/v1/messages/count_tokens"):
            assert client.post(path, json=body, headers=KEY).status_code == 200
    for sent in upstream.requests:
        raw = sent.content.decode()
        decoded = json.dumps(json.loads(raw), ensure_ascii=False)
        for value in VALUES:
            assert value not in raw
            assert compact(value) not in compact(decoded)


# --- Review fixes -----------------------------------------------------------------------


def test_upstream_2xx_status_is_kept(upstream: FakeUpstream) -> None:
    upstream.handler = lambda request: httpx.Response(
        201, json={"content": [{"type": "text", "text": "x"}]}
    )
    for client in _client(upstream):
        assert client.post("/v1/messages", json=_msg("hola"), headers=KEY).status_code == 201


@pytest.mark.parametrize("path", ["/v1/messages", "/v1/messages/count_tokens"])
def test_lone_surrogate_is_a_400(proxy: TestClient, upstream: FakeUpstream, path: str) -> None:
    raw = b'{"model": "m", "messages": [{"role": "user", "content": "a\ud800b"}]}'
    response = proxy.post(path, content=raw, headers={**KEY, "Content-Type": "application/json"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert upstream.requests == []


@pytest.mark.parametrize(
    ("value", "code"), [("yes", "invalid_request"), (True, "streaming_not_supported")]
)
def test_count_tokens_checks_stream(
    proxy: TestClient, upstream: FakeUpstream, value: object, code: str
) -> None:
    response = proxy.post(
        "/v1/messages/count_tokens", json={**_msg("hola"), "stream": value}, headers=KEY
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == code
    assert upstream.requests == []


def test_placeholder_in_thinking_is_not_reused_end_to_end(upstream: FakeUpstream) -> None:
    thinking = {**THINKING, "thinking": "antes vi [[ES_DNI_1]]"}
    body = {
        "model": "m",
        "messages": [
            {"role": "assistant", "content": [thinking, {"type": "text", "text": "vale"}]},
            {"role": "user", "content": f"mi DNI {SENTINEL_DNI}"},
        ],
    }
    upstream.handler = lambda request: httpx.Response(
        200, json={"content": [{"type": "text", "text": "[[ES_DNI_1]] / [[ES_DNI_2]]"}]}
    )
    for client in _client(upstream):
        response = client.post("/v1/messages", json=body, headers=KEY)

        assert response.json()["content"][0]["text"] == f"[[ES_DNI_1]] / {SENTINEL_DNI}"
