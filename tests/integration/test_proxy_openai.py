"""POST /v1/chat/completions against a fake upstream (httpx.MockTransport). No real LLM calls."""

import json
import logging
from collections.abc import Callable, Iterator, Sequence

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from antifaz import Span, guard
from antifaz.api.app import create_app
from antifaz.config import Settings, UnsafeConfigError
from antifaz.detect.scan import scan
from tests.conftest import SENTINEL_DNI
from tests.integration.fakes import (
    GATEWAY_KEY,
    PROVIDER_KEY,
    FakeUpstream,
    Handler,
    compact,
    misses_repeats,
    openai_sse,
    openai_text,
    pieces_of,
    raiser,
    sse_payloads,
    sse_response,
)

UPSTREAM = "https://upstream.invalid/v1"
AUTH = {"Authorization": f"Bearer {GATEWAY_KEY}"}


def echo(request: httpx.Request) -> httpx.Response:
    """Answers with the last user message as the assistant content (placeholders included)."""
    body = json.loads(request.content)
    content = body["messages"][-1]["content"]
    if body.get("stream"):  # the same answer as server-sent events, in pieces of 3 characters
        text = content if isinstance(content, str) else json.dumps(content)
        return sse_response(openai_sse(pieces_of(text)))[0]
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        },
    )


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "antifaz_api_key": SecretStr(GATEWAY_KEY),
        "openai_api_key": SecretStr(PROVIDER_KEY),
        "openai_base_url": UPSTREAM,
        "allowed_hosts": ["testserver"],
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


def _chat(content: object) -> dict[str, object]:
    return {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": content}]}


# --- Auth -------------------------------------------------------------------------------


def test_missing_key_is_rejected(proxy: TestClient, upstream: FakeUpstream) -> None:
    response = proxy.post("/v1/chat/completions", json=_chat("hola"))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert upstream.requests == []


@pytest.mark.parametrize(
    "header",
    ["Bearer wrong", f"Basic {GATEWAY_KEY}", GATEWAY_KEY, "Bearer ", f"Bearer {GATEWAY_KEY}x"],
)
def test_wrong_key_is_rejected(proxy: TestClient, upstream: FakeUpstream, header: str) -> None:
    response = proxy.post(
        "/v1/chat/completions", json=_chat("hola"), headers={"Authorization": header}
    )

    assert response.status_code == 401
    assert GATEWAY_KEY not in response.text
    assert upstream.requests == []


def test_gateway_without_key_configured_does_not_start() -> None:
    with pytest.raises(UnsafeConfigError):
        create_app(_settings(antifaz_api_key=None))


def test_gateway_without_provider_key_refuses(upstream: FakeUpstream) -> None:
    for client in _client(upstream, _settings(openai_api_key=None)):
        response = client.post("/v1/chat/completions", json=_chat("hola"), headers=AUTH)

        assert response.status_code == 503
        assert upstream.requests == []


def test_right_key_reaches_upstream_with_provider_key_only(
    proxy: TestClient, upstream: FakeUpstream
) -> None:
    response = proxy.post(
        "/v1/chat/completions",
        json=_chat("hola"),
        headers={**AUTH, "X-Custom": "client", "OpenAI-Organization": "org-client"},
    )

    assert response.status_code == 200
    (sent,) = upstream.requests
    assert sent.headers["Authorization"] == f"Bearer {PROVIDER_KEY}"
    assert GATEWAY_KEY not in str(sent.headers)
    assert "x-custom" not in sent.headers
    assert "openai-organization" not in sent.headers
    assert GATEWAY_KEY.encode() not in sent.content


# --- Destination --------------------------------------------------------------------------


def test_client_cannot_choose_the_destination(proxy: TestClient, upstream: FakeUpstream) -> None:
    body = {**_chat("hola"), "base_url": "http://169.254.169.254/"}
    response = proxy.post(
        "/v1/chat/completions?base_url=http://evil.invalid",
        json=body,
        headers={**AUTH, "X-Forwarded-Host": "evil.invalid", "X-Base-URL": "http://evil.invalid"},
    )

    assert response.status_code == 200
    (sent,) = upstream.requests
    assert str(sent.url) == f"{UPSTREAM}/chat/completions"


# --- Masking and restore ------------------------------------------------------------------


def test_round_trip_restores_values_for_the_client(
    proxy: TestClient, upstream: FakeUpstream
) -> None:
    response = proxy.post(
        "/v1/chat/completions", json=_chat(f"Mi DNI es {SENTINEL_DNI}"), headers=AUTH
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == f"Mi DNI es {SENTINEL_DNI}"
    (sent,) = upstream.requests
    assert SENTINEL_DNI.encode() not in sent.content
    assert b"[[ES_DNI_1]]" in sent.content


def test_tool_call_arguments_round_trip(upstream: FakeUpstream) -> None:
    def tool_echo(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        arguments = body["messages"][-1]["tool_calls"][0]["function"]["arguments"]
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "f", "arguments": arguments}}
            ],
        }
        return httpx.Response(200, json={"choices": [{"index": 0, "message": message}]})

    upstream.handler = tool_echo
    arguments = json.dumps({"dni": SENTINEL_DNI, "nota": 'con "comillas" y \\ barra'})
    body = {
        "model": "m",
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "c0",
                        "type": "function",
                        "function": {"name": "f", "arguments": arguments},
                    }
                ],
            }
        ],
    }
    for client in _client(upstream):
        response = client.post("/v1/chat/completions", json=body, headers=AUTH)

        assert response.status_code == 200
        got = response.json()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        assert json.loads(got) == json.loads(arguments)
        assert SENTINEL_DNI.encode() not in upstream.requests[0].content


def test_streaming_round_trip_restores_content(proxy: TestClient, upstream: FakeUpstream) -> None:
    text = f"Mi DNI es {SENTINEL_DNI} y el correo ana@example.com."
    response = proxy.post(
        "/v1/chat/completions", json={**_chat(text), "stream": True}, headers=AUTH
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert openai_text(response.text) == text
    assert response.text.endswith("data: [DONE]\n\n")
    (sent,) = upstream.requests
    assert SENTINEL_DNI.encode() not in sent.content
    assert json.loads(sent.content)["stream"] is True


@pytest.mark.parametrize("options", [{"include_usage": True}, {"include_obfuscation": False}, None])
def test_stream_options_in_the_allowlist_pass(
    proxy: TestClient, upstream: FakeUpstream, options: object
) -> None:
    body = {**_chat("hola"), "stream": True, "stream_options": options}
    response = proxy.post("/v1/chat/completions", json=body, headers=AUTH)

    assert response.status_code == 200
    assert json.loads(upstream.requests[0].content)["stream_options"] == options


@pytest.mark.parametrize(
    "options",
    [{"include_usage": "yes"}, {"include_usage": True, "x": "12345678Z"}, "include_usage", []],
)
def test_other_stream_options_are_refused(
    proxy: TestClient, upstream: FakeUpstream, options: object
) -> None:
    body = {**_chat("hola"), "stream": True, "stream_options": options}
    response = proxy.post("/v1/chat/completions", json=body, headers=AUTH)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert "12345678Z" not in response.text
    assert upstream.requests == []


def test_stream_answered_with_json_is_restored_as_json(upstream: FakeUpstream) -> None:
    def json_answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        body.pop("stream")  # a provider that ignores `stream` and answers JSON
        return echo(httpx.Request("POST", request.url, json=body))

    upstream.handler = json_answer
    for client in _client(upstream):
        response = client.post(
            "/v1/chat/completions",
            json={**_chat(f"DNI {SENTINEL_DNI}"), "stream": True},
            headers=AUTH,
        )
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == f"DNI {SENTINEL_DNI}"


def test_stream_tool_call_arguments_round_trip(upstream: FakeUpstream) -> None:
    arguments = json.dumps({"dni": SENTINEL_DNI, "nota": 'dijo "hola" \\ y ya'})

    def tool_stream(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)["messages"][0]["tool_calls"][0]["function"]["arguments"]
        head = {"id": "c", "object": "chat.completion.chunk"}
        calls = [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "f"}}]
        calls += [
            {"index": 0, "function": {"arguments": sent[i : i + 4]}} for i in range(0, len(sent), 4)
        ]
        events = [{**head, "choices": [{"index": 0, "delta": {"tool_calls": [c]}}]} for c in calls]
        events.append(
            {**head, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
        )
        text = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
        return sse_response(text, split=5)[0]

    upstream.handler = tool_stream
    call = {"id": "call_0", "type": "function", "function": {"name": "f", "arguments": arguments}}
    body = {"model": "m", "stream": True, "messages": [{"role": "assistant", "tool_calls": [call]}]}
    for client in _client(upstream):
        response = client.post("/v1/chat/completions", json=body, headers=AUTH)
        assert response.status_code == 200
        pieces = [
            c["function"].get("arguments", "")
            for p in sse_payloads(response.text)
            if isinstance(p, dict)
            for choice in p["choices"]
            for c in choice["delta"].get("tool_calls", [])
        ]
        assert json.loads("".join(pieces)) == json.loads(arguments)
    assert SENTINEL_DNI.encode() not in upstream.requests[0].content


def test_attachment_is_blocked(proxy: TestClient, upstream: FakeUpstream) -> None:
    content = [
        {"type": "text", "text": SENTINEL_DNI},
        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
    ]
    response = proxy.post("/v1/chat/completions", json=_chat(content), headers=AUTH)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "antifaz_blocked"
    assert SENTINEL_DNI not in response.text
    assert upstream.requests == []


def test_detector_failure_blocks(upstream: FakeUpstream) -> None:
    def broken(_: str) -> Sequence[Span]:
        raise RuntimeError(SENTINEL_DNI)

    for client in _client(upstream, detector=broken):
        response = client.post("/v1/chat/completions", json=_chat(SENTINEL_DNI), headers=AUTH)

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "antifaz_blocked"
        assert SENTINEL_DNI not in response.text
        assert upstream.requests == []


def test_guard_blocks_what_the_masker_missed(upstream: FakeUpstream) -> None:
    for client in _client(upstream, detector=misses_repeats):
        response = client.post(
            "/v1/chat/completions",
            json=_chat(f"{SENTINEL_DNI} y {SENTINEL_DNI}"),
            headers=AUTH,
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "antifaz_blocked"
        assert SENTINEL_DNI not in response.text
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

    response = proxy.post("/v1/chat/completions", json=_chat(f"DNI {SENTINEL_DNI}"), headers=AUTH)

    assert response.status_code == 200
    assert seen == [upstream.requests[0].content]


def test_invalid_json_is_rejected_without_echo(proxy: TestClient, upstream: FakeUpstream) -> None:
    for raw in (b"{" + SENTINEL_DNI.encode(), b"[1, 2]", b"\xff\xfe"):
        response = proxy.post(
            "/v1/chat/completions",
            content=raw,
            headers={**AUTH, "Content-Type": "application/json"},
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_request"
        assert SENTINEL_DNI not in response.text
    assert upstream.requests == []


def test_too_large_body_is_rejected(upstream: FakeUpstream) -> None:
    for client in _client(upstream, _settings(max_body_bytes=100)):
        response = client.post("/v1/chat/completions", json=_chat("a" * 200), headers=AUTH)

        assert response.status_code == 413
        assert upstream.requests == []


# --- Upstream errors ----------------------------------------------------------------------


def test_upstream_error_is_passed_through(upstream: FakeUpstream) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"message": "rate limit", "type": "rate_limit"}},
            headers={"x-provider-secret-header": "1"},
        )

    upstream.handler = fail
    for client in _client(upstream):
        response = client.post("/v1/chat/completions", json=_chat("hola"), headers=AUTH)

        assert response.status_code == 429
        assert response.json() == {"error": {"message": "rate limit", "type": "rate_limit"}}
        assert "x-provider-secret-header" not in response.headers


@pytest.mark.parametrize(
    ("error", "status"),
    [(httpx.ReadTimeout, 504), (httpx.ConnectTimeout, 504), (httpx.ConnectError, 502)],
)
def test_transport_errors_have_fixed_messages(
    upstream: FakeUpstream, error: type[httpx.HTTPError], status: int
) -> None:
    upstream.handler = raiser(error)
    for client in _client(upstream):
        response = client.post("/v1/chat/completions", json=_chat("hola"), headers=AUTH)

        assert response.status_code == status
        assert SENTINEL_DNI not in response.text
        assert "boom" not in response.text


def test_non_json_upstream_answer_is_a_502(upstream: FakeUpstream) -> None:
    upstream.handler = lambda request: httpx.Response(
        200, content=b"<html>" + SENTINEL_DNI.encode()
    )
    for client in _client(upstream):
        response = client.post("/v1/chat/completions", json=_chat("hola"), headers=AUTH)

        assert response.status_code == 502
        assert SENTINEL_DNI not in response.text


# --- Invariant 8: the sentinel never reaches logs or error bodies --------------------------


def test_sentinel_never_in_logs_or_error_bodies(
    upstream: FakeUpstream, caplog: pytest.LogCaptureFixture
) -> None:
    for name in ("", "httpx", "httpcore", "http", "uvicorn", "fastapi", "antifaz"):
        caplog.set_level(logging.DEBUG, logger=name or None)
    bodies: list[str] = []

    def answer_with_sentinel(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"index": 0, "message": {"content": f"clear {SENTINEL_DNI}"}}]},
        )

    def error_echoing_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=request.content)

    scenarios: list[tuple[Handler, Callable[[str], Sequence[Span]]]] = [
        (echo, scan),
        (answer_with_sentinel, scan),
        (error_echoing_request, scan),
        (raiser(httpx.ReadTimeout), scan),
        (echo, misses_repeats),  # guard block
    ]
    for handler, detector in scenarios:
        upstream.handler = handler
        for client in _client(upstream, detector=detector):
            response = client.post(
                "/v1/chat/completions",
                json=_chat(f"{SENTINEL_DNI} {SENTINEL_DNI}"),
                headers=AUTH,
            )
            if response.status_code >= 400:
                bodies.append(response.text)

    assert len(bodies) == 3
    for body in bodies:
        assert SENTINEL_DNI not in body
    logs = caplog.text + "".join(str(r.__dict__) for r in caplog.records)
    for secret in (SENTINEL_DNI, GATEWAY_KEY, PROVIDER_KEY):
        assert secret not in logs


def test_httpx_loggers_are_quiet_by_default() -> None:
    create_app(_settings())

    # httpx2/httpcore2 too: Starlette's TestClient (and the official SDKs) use them when
    # installed, and at INFO they write every URL, keys pasted in a path included.
    for name in ("httpx", "httpcore", "httpx2", "httpcore2"):
        assert logging.getLogger(name).level == logging.WARNING, name


# --- Invariant 2 end to end: guard OFF, the upstream still gets no hidden value -----------

VALUES = ["12345678Z", "X1234567L", "ana@example.com", "ES9121000418450200051332"]


def test_invariant_2_without_guard(upstream: FakeUpstream, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guard, "check", lambda payload, vault: None)  # ONLY in this test

    arguments = json.dumps({"dni": VALUES[0], "otros": [VALUES[1], {"mail": VALUES[2]}]})
    body = {
        "model": "m",
        "messages": [
            {"role": "system", "content": f"Cliente {VALUES[3]}"},
            {"role": "user", "content": [{"type": "text", "text": f"soy {VALUES[0]}"}]},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "c",
                        "type": "function",
                        "function": {"name": "f", "arguments": arguments},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c", "content": f"resultado {VALUES[2]}"},
            {"role": "user", "content": "gracias"},
        ],
        "campo_nuevo": f"{VALUES[1]}",
        "user": VALUES[2],
    }
    for client in _client(upstream):
        response = client.post("/v1/chat/completions", json=body, headers=AUTH)

        assert response.status_code == 200
    (sent,) = upstream.requests
    raw = sent.content.decode()
    decoded = json.dumps(json.loads(raw), ensure_ascii=False)
    for value in VALUES:
        assert value not in raw
        assert value not in decoded
        assert compact(value) not in compact(decoded)


def test_placeholders_from_another_request_are_not_restored(
    proxy: TestClient, upstream: FakeUpstream
) -> None:
    proxy.post("/v1/chat/completions", json=_chat(f"DNI {SENTINEL_DNI}"), headers=AUTH)

    response = proxy.post("/v1/chat/completions", json=_chat("hola"), headers=AUTH)

    upstream.handler = lambda request: httpx.Response(
        200, json={"choices": [{"message": {"content": "[[ES_DNI_1]]"}}]}
    )
    response = proxy.post("/v1/chat/completions", json=_chat("sin datos"), headers=AUTH)
    assert response.json()["choices"][0]["message"]["content"] == "[[ES_DNI_1]]"


def test_email_detector_type_is_used(upstream: FakeUpstream) -> None:
    for client in _client(upstream):
        response = client.post("/v1/chat/completions", json=_chat("ana@example.com"), headers=AUTH)

        assert response.status_code == 200
        assert b"[[EMAIL_1]]" in upstream.requests[0].content


@pytest.mark.parametrize("raw", [b'{"messages": [], "t": NaN}', b'{"messages": [], "t": Infinity}'])
def test_nan_and_infinity_are_rejected(
    proxy: TestClient, upstream: FakeUpstream, raw: bytes
) -> None:
    response = proxy.post(
        "/v1/chat/completions", content=raw, headers={**AUTH, "Content-Type": "application/json"}
    )

    assert response.status_code == 400
    assert upstream.requests == []


@pytest.mark.parametrize("value", ["true", 1, None, "yes"])
def test_stream_must_be_a_boolean(proxy: TestClient, upstream: FakeUpstream, value: object) -> None:
    response = proxy.post(
        "/v1/chat/completions", json={**_chat("hola"), "stream": value}, headers=AUTH
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert upstream.requests == []


def test_stream_false_is_accepted(proxy: TestClient) -> None:
    response = proxy.post(
        "/v1/chat/completions", json={**_chat("hola"), "stream": False}, headers=AUTH
    )

    assert response.status_code == 200


def test_upstream_redirect_is_a_502(upstream: FakeUpstream) -> None:
    upstream.handler = lambda request: httpx.Response(
        302, headers={"Location": "http://evil.invalid"}
    )
    for client in _client(upstream):
        response = client.post("/v1/chat/completions", json=_chat("hola"), headers=AUTH)

        assert response.status_code == 502
        assert response.json()["error"]["code"] == "upstream_redirect"
        assert "evil" not in response.text
    assert len(upstream.requests) == 1


def test_injected_http_client_is_not_closed(upstream: FakeUpstream) -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    with TestClient(create_app(_settings(), http_client=http)):
        pass

    assert not http.is_closed


def test_own_http_client_is_closed() -> None:
    app = create_app(_settings())
    with TestClient(app):
        client = app.state.http_client

    assert client.is_closed


def test_upstream_2xx_status_is_kept(upstream: FakeUpstream) -> None:
    upstream.handler = lambda request: httpx.Response(
        201, json={"choices": [{"message": {"content": "x"}}]}
    )
    for client in _client(upstream):
        assert (
            client.post("/v1/chat/completions", json=_chat("hola"), headers=AUTH).status_code == 201
        )


def test_lone_surrogate_is_a_400(proxy: TestClient, upstream: FakeUpstream) -> None:
    raw = b'{"model": "m", "messages": [{"role": "user", "content": "a\ud800b"}]}'
    response = proxy.post(
        "/v1/chat/completions", content=raw, headers={**AUTH, "Content-Type": "application/json"}
    )

    assert response.status_code == 400
    assert upstream.requests == []
