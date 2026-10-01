"""Red team of the v0.1 routes (issue 29): GET /v1/models and POST /antifaz/scan.

/v1/models: the client may only add a few allowlisted query parameters and two Anthropic
headers; nothing else reaches the provider, and a model list that carries a key is dropped.
/antifaz/scan: the answer holds only types and positions, never a piece of the text.
All values are synthetic.
"""

import base64
import json
import logging
from collections.abc import Callable, Iterator, Sequence

import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from antifaz.detect.ner.cache import SpanCache
from antifaz.detect.ner.engine import NerDetector
from antifaz.detect.scan import Scanner
from antifaz.detect.types import Confidence, EntityType, Layer, Span
from tests.integration.fakes import GATEWAY_KEY, PROVIDER_KEY, FakeUpstream, models_list
from tests.nerfakes import CARMEN, InProcess
from tests.redteam.conftest import DNI, IBAN, NIE, PHONE, gateway_client, gateway_settings

OPENAI = {"Authorization": f"Bearer {GATEWAY_KEY}"}
ANTHROPIC = {"x-api-key": GATEWAY_KEY, "anthropic-version": "2023-06-01"}
EMAIL = "ana.garcia@example.com"


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream(models_list)


@pytest.fixture
def client(upstream: FakeUpstream) -> Iterator[TestClient]:
    yield from gateway_client(upstream, gateway_settings(max_body_bytes=4096))


def _refused(response: httpx.Response, code: str = "invalid_query") -> None:
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code


# --- GET /v1/models: query parameters ----------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "x=1",
        "limit=1&limit=2",  # repeated: two readers could pick different copies
        "after_id=a&after_id=b",
        "limit=0",
        "limit=1001",
        "limit=-1",
        "limit=1e3",
        "limit=01",
        "limit=%EF%BC%91",  # a full-width "1"
        "limit=",
        "limit",
        "after_id=",
        "after_id=a%0d%0aX-Injected:%201",  # CRLF
        "after_id=a%26limit%3D5",  # an encoded "&" that would add a parameter
        "after_id=..%2F..%2Fadmin",
        "after_id=http://169.254.169.254/",
        "after_id=a@evil.example",
        "after_id=a%23frag",
        "after_id=a%20b",
        "after_id=%E2%80%8Bclaude",  # zero-width space
        "after_id=" + "a" * 201,
        "beta=true",
        "LIMIT=5",
    ],
)
def test_anthropic_query_outside_the_allowlist_is_refused(
    client: TestClient, upstream: FakeUpstream, query: str
) -> None:
    _refused(client.get(f"/v1/models?{query}", headers=ANTHROPIC))
    assert upstream.requests == []


@pytest.mark.parametrize("query", ["limit=5", "after_id=gpt-x", "x=1", "api-version=1"])
def test_the_openai_route_takes_no_query_parameter(
    client: TestClient, upstream: FakeUpstream, query: str
) -> None:
    _refused(client.get(f"/v1/models?{query}", headers=OPENAI))
    assert upstream.requests == []


def test_the_query_sent_is_rebuilt_from_the_checked_values(
    client: TestClient, upstream: FakeUpstream
) -> None:
    response = client.get("/v1/models?limit=1000&before_id=claude-x", headers=ANTHROPIC)

    assert response.status_code == 200
    assert upstream.requests[0].url.query == b"limit=1000&before_id=claude-x"


@pytest.mark.parametrize("value", [DNI, NIE, IBAN, PHONE])
def test_personal_data_in_a_cursor_is_blocked(
    client: TestClient, upstream: FakeUpstream, value: str
) -> None:
    response = client.get("/v1/models", params={"after_id": value}, headers=ANTHROPIC)

    _refused(response, "antifaz_blocked")
    assert value not in response.text
    assert upstream.requests == []


def test_an_email_in_a_cursor_is_refused(client: TestClient, upstream: FakeUpstream) -> None:
    # "@" is not an id character: refused before the detector even runs.
    response = client.get("/v1/models", params={"after_id": EMAIL}, headers=ANTHROPIC)

    _refused(response)
    assert EMAIL not in response.text
    assert upstream.requests == []


@pytest.mark.parametrize("key", [GATEWAY_KEY, PROVIDER_KEY])
def test_a_configured_key_in_the_query_is_refused(
    client: TestClient, upstream: FakeUpstream, key: str
) -> None:
    response = client.get("/v1/models", params={"after_id": key}, headers=ANTHROPIC)

    _refused(response)
    assert key not in response.text
    assert upstream.requests == []


def test_a_broken_detector_blocks_the_query(upstream: FakeUpstream) -> None:
    def broken(text: str) -> Sequence[Span]:
        raise RuntimeError(text)

    for client in gateway_client(upstream, detector=broken):
        response = client.get("/v1/models", params={"after_id": "claude-x"}, headers=ANTHROPIC)

        _refused(response, "antifaz_blocked")
        assert "claude-x" not in response.text
    assert upstream.requests == []


def test_the_destination_never_comes_from_the_client(
    client: TestClient, upstream: FakeUpstream
) -> None:
    headers = {
        **OPENAI,
        "X-Forwarded-Host": "evil.example",
        "Forwarded": "host=evil.example",
        "X-Original-URL": "http://evil.example/",
    }

    assert client.get("/v1/models", headers=headers).status_code == 200
    assert str(upstream.requests[0].url) == "https://upstream.invalid/v1/models"


# --- GET /v1/models: headers -------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {"anthropic-version": "2023-06-01 x"},
        {"anthropic-version": "2023-06-01;x=1"},
        {"anthropic-version": ""},
        {"anthropic-version": "a" * 201},
        {"anthropic-beta": "a b"},
    ],
)
def test_bad_anthropic_headers_are_refused(
    client: TestClient, upstream: FakeUpstream, headers: dict[str, str]
) -> None:
    response = client.get("/v1/models", headers={**ANTHROPIC, **headers})

    _refused(response, "invalid_header")
    assert upstream.requests == []


def test_a_repeated_anthropic_header_is_refused(client: TestClient, upstream: FakeUpstream) -> None:
    headers = [
        ("x-api-key", GATEWAY_KEY),
        ("anthropic-version", "2023-06-01"),
        ("anthropic-version", "2024-01-01"),
    ]

    response = client.get("/v1/models", headers=headers)

    _refused(response, "invalid_header")
    assert upstream.requests == []


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_no_other_client_header_reaches_the_provider(
    client: TestClient, upstream: FakeUpstream, provider: str
) -> None:
    auth = ANTHROPIC if provider == "anthropic" else OPENAI
    extra = {
        "Cookie": "session=abc",
        "X-Custom": "hola",
        "OpenAI-Organization": "org-x",
        "anthropic-dangerous-direct-browser-access": "true",
        "X-Request-ID": DNI,
    }

    assert client.get("/v1/models", headers={**auth, **extra}).status_code == 200
    sent = {name.lower() for name in upstream.requests[0].headers}
    allowed = {"host", "accept", "accept-encoding", "connection", "user-agent"}
    allowed |= {"x-api-key", "anthropic-version"} if provider == "anthropic" else {"authorization"}
    assert sent <= allowed
    assert GATEWAY_KEY not in str(upstream.requests[0].headers)
    assert DNI not in str(upstream.requests[0].headers)


def test_anthropic_beta_alone_does_not_route_to_anthropic(
    client: TestClient, upstream: FakeUpstream
) -> None:
    assert client.get("/v1/models", headers={**OPENAI, "anthropic-beta": "x"}).status_code == 200
    assert upstream.requests[0].url.host == "upstream.invalid"
    assert "anthropic-beta" not in upstream.requests[0].headers


# --- GET /v1/models: the answer ----------------------------------------------------------


@pytest.mark.parametrize("escaped", [False, True])
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_a_model_list_that_carries_the_provider_key_is_dropped(
    upstream: FakeUpstream, escaped: bool, provider: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        key = "".join(f"\\u{ord(c):04x}" for c in PROVIDER_KEY) if escaped else PROVIDER_KEY
        body = '{"object": "list", "data": [{"id": "gpt-' + key + '"}]}'
        return httpx.Response(200, content=body.encode(), headers={"content-type": "x"})

    upstream.handler = handler
    for client in gateway_client(upstream):
        auth = ANTHROPIC if provider == "anthropic" else OPENAI
        response = client.get("/v1/models", headers=auth)

        assert response.status_code == 502
        assert response.json()["error"]["code"] == "bad_upstream_response"
        assert PROVIDER_KEY not in response.text


def test_provider_response_headers_are_not_relayed(upstream: FakeUpstream) -> None:
    upstream.handler = lambda request: httpx.Response(
        200, json={"data": []}, headers={"Set-Cookie": "a=b", "X-Upstream": "1"}
    )
    for client in gateway_client(upstream):
        response = client.get("/v1/models", headers=OPENAI)

        assert response.status_code == 200
        assert "set-cookie" not in response.headers
        assert "x-upstream" not in response.headers


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "HEAD"])
def test_only_get_lists_models(client: TestClient, upstream: FakeUpstream, method: str) -> None:
    assert client.request(method, "/v1/models", headers=OPENAI, json={}).status_code == 405
    assert upstream.requests == []


# --- POST /antifaz/scan ------------------------------------------------------------------

_values = st.sampled_from([DNI, NIE, IBAN, PHONE, EMAIL])
_noise = st.text(alphabet=st.characters(codec="utf-8", exclude_categories=["Cs"]), max_size=30)


@settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(before=_noise, value=_values, after=_noise)
def test_scan_never_returns_a_piece_of_the_text(
    client: TestClient, before: str, value: str, after: str
) -> None:
    text = f"{before} {value} {after}"

    response = client.post("/antifaz/scan", json={"text": text}, headers=OPENAI)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"entities"}
    types: set[str] = set()
    for entity in body["entities"]:
        assert set(entity) == {"type", "start", "end"}
        assert entity["type"] in set(EntityType)
        assert type(entity["start"]) is int
        assert type(entity["end"]) is int
        assert 0 <= entity["start"] < entity["end"] <= len(text)
        types.add(entity["type"])
    # Without the field names and the type names, only JSON punctuation and numbers are left.
    leftover = response.text
    for word in sorted({"entities", "type", "start", "end", *types}, key=len, reverse=True):
        leftover = leftover.replace(word, "")
    assert set(leftover) <= set('{}[]":, 0123456789')


@pytest.mark.parametrize(
    "body",
    [
        {"texts": [DNI]},
        {"text": DNI, "texts": [DNI]},
        {"text": [DNI]},
        {"text": {"type": "text", "text": DNI}},
        {"text": DNI, "image_url": {"url": "data:image/png;base64,AAAA"}},
        {"text": DNI, "source": {"type": "base64", "data": "AAAA"}},
        {"text": DNI, "file_id": "file-1"},
        {"text": None},
        {"text": 12345678},
        {"Text": DNI},
        {},
    ],
)
def test_scan_takes_only_one_text(client: TestClient, body: object) -> None:
    response = client.post("/antifaz/scan", json=body, headers=OPENAI)

    _refused(response, "invalid_request")
    assert DNI not in response.text


@pytest.mark.parametrize(
    "raw",
    [
        b'{"text": "a", "text": "' + DNI.encode() + b'"}',
        b'{"text": NaN}',
        b"[" + json.dumps(DNI).encode() + b"]",
        json.dumps(DNI).encode(),
        b'{"text": "\\ud800"}',
        b'{"text": "' + DNI.encode(),
        '{"text": "\xe9"}'.encode("latin-1"),
    ],
)
def test_scan_uses_the_proxy_json_rules(client: TestClient, raw: bytes) -> None:
    headers = {**OPENAI, "Content-Type": "application/json"}

    response = client.post("/antifaz/scan", content=raw, headers=headers)

    _refused(response, "invalid_request")
    assert DNI not in response.text


def test_a_huge_text_is_refused(client: TestClient) -> None:
    response = client.post("/antifaz/scan", json={"text": "a" * 5000}, headers=OPENAI)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_scan_needs_json(client: TestClient) -> None:
    headers = {**OPENAI, "Content-Type": "text/plain"}

    assert client.post("/antifaz/scan", content=DNI, headers=headers).status_code == 415


class _BrokenNer(InProcess):
    """A NER whose model fails with the text in its error message."""

    def __init__(self) -> None:
        super().__init__(backend=None)  # type: ignore[arg-type]

    def predict(self, texts: Sequence[str], *args: object, **kwargs: object) -> object:
        raise RuntimeError(f"model failed on {texts}")


def test_a_failing_ner_blocks_the_scan(
    upstream: FakeUpstream, caplog: pytest.LogCaptureFixture
) -> None:
    ner = NerDetector(_BrokenNer(), cache=SpanCache(10))
    caplog.set_level(logging.DEBUG)
    for client in gateway_client(upstream, detector=Scanner(ner)):
        response = client.post("/antifaz/scan", json={"text": f"{CARMEN} {DNI}"}, headers=OPENAI)

        _refused(response, "antifaz_blocked")
        assert CARMEN not in response.text
        assert DNI not in response.text
    assert CARMEN not in caplog.text
    assert DNI not in caplog.text


def test_malformed_spans_block_the_scan(upstream: FakeUpstream) -> None:
    def outside(text: str) -> Sequence[Span]:
        return [Span(0, len(text) + 5, EntityType.ES_DNI, Layer.VALIDATOR, Confidence.HIGH)]

    for client in gateway_client(upstream, detector=outside):
        response = client.post("/antifaz/scan", json={"text": DNI}, headers=OPENAI)

        _refused(response, "antifaz_blocked")


def test_the_scan_answer_is_never_logged(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    client.post("/antifaz/scan", json={"text": f"DNI {DNI}"}, headers=OPENAI)

    assert DNI not in caplog.text
    assert "ES_DNI" not in caplog.text


@pytest.mark.parametrize("path", ["/v1/messages", "/v1/messages/count_tokens"])
@pytest.mark.parametrize("name", ["anthropic-version", "anthropic-beta"])
def test_a_repeated_anthropic_header_is_refused_on_messages_too(
    client: TestClient, upstream: FakeUpstream, path: str, name: str
) -> None:
    headers = [("x-api-key", GATEWAY_KEY), ("content-type", "application/json")]
    headers += [("anthropic-version", "2023-06-01"), (name, "a"), (name, "b")]
    body = {"model": "m", "max_tokens": 8, "messages": [{"role": "user", "content": "hola"}]}

    response = client.post(path, content=json.dumps(body), headers=headers)

    _refused(response, "invalid_header")
    assert upstream.requests == []


# --- Review fixes (issue 29) -------------------------------------------------------------

MESSAGES_BODY = {"model": "m", "max_tokens": 8, "messages": [{"role": "user", "content": "hola"}]}


@pytest.mark.parametrize("key", [GATEWAY_KEY, PROVIDER_KEY])
@pytest.mark.parametrize("name", ["anthropic-version", "anthropic-beta"])
@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/v1/models"), ("POST", "/v1/messages"), ("POST", "/v1/messages/count_tokens")],
)
def test_a_key_in_an_anthropic_header_is_refused(
    client: TestClient, upstream: FakeUpstream, key: str, name: str, method: str, path: str
) -> None:
    headers = {**ANTHROPIC, name: key}
    body = MESSAGES_BODY if method == "POST" else None

    response = client.request(method, path, json=body, headers=headers)

    _refused(response, "invalid_header")
    assert key not in response.text
    assert upstream.requests == []


def test_a_key_split_between_the_two_anthropic_headers_is_refused(
    client: TestClient, upstream: FakeUpstream
) -> None:
    headers = {
        "x-api-key": GATEWAY_KEY,
        "anthropic-version": GATEWAY_KEY[:16],
        "anthropic-beta": GATEWAY_KEY[16:],
    }

    _refused(client.get("/v1/models", headers=headers), "invalid_header")
    assert upstream.requests == []


@pytest.mark.parametrize("key", [GATEWAY_KEY, PROVIDER_KEY])
@pytest.mark.parametrize("reverse", [False, True])
def test_a_key_split_across_query_values_is_refused(
    client: TestClient, upstream: FakeUpstream, key: str, reverse: bool
) -> None:
    first, second = key[:10], key[10:]
    params = (
        [("before_id", second), ("after_id", first)]
        if reverse
        else [
            ("after_id", first),
            ("before_id", second),
        ]
    )

    response = client.get("/v1/models", params=params, headers=ANTHROPIC)

    _refused(response)
    assert upstream.requests == []


@pytest.mark.parametrize(
    ("method", "path"), [("POST", "/v1/models"), ("GET", "/antifaz/scan"), ("PUT", "/v1/messages")]
)
def test_a_wrong_method_answers_405_in_the_error_format(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, path, headers=ANTHROPIC, json={})

    assert response.status_code == 405
    assert response.json() == {
        "error": {"code": "method_not_allowed", "message": "method not allowed on this route"}
    }
    assert "allow" in response.headers


def test_an_unknown_path_with_the_key_answers_404_in_the_error_format(client: TestClient) -> None:
    response = client.get(f"/v1/{DNI}", headers=OPENAI)

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "no such route"}}
    assert DNI not in response.text


@pytest.mark.parametrize(
    ("method", "path", "auth"),
    [("GET", "/v1/models", OPENAI), ("POST", "/v1/messages", ANTHROPIC)],
)
def test_a_provider_error_in_json_is_returned_as_json(
    upstream: FakeUpstream, method: str, path: str, auth: dict[str, str]
) -> None:
    error = {"error": {"message": "<script>x</script>"}}
    upstream.handler = lambda request: httpx.Response(
        429, content=json.dumps(error).encode(), headers={"content-type": "text/html"}
    )
    for client in gateway_client(upstream):
        body = MESSAGES_BODY if method == "POST" else None
        response = client.request(method, path, json=body, headers=auth)

        assert response.status_code == 429
        assert response.headers["content-type"] == "application/json"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.json() == error


@pytest.mark.parametrize(
    ("method", "path", "auth"),
    [("GET", "/v1/models", OPENAI), ("POST", "/v1/messages", ANTHROPIC)],
)
def test_a_provider_error_that_is_not_json_gives_a_fixed_502(
    upstream: FakeUpstream, method: str, path: str, auth: dict[str, str]
) -> None:
    upstream.handler = lambda request: httpx.Response(
        503, content=b"<html><script>x</script></html>", headers={"content-type": "text/html"}
    )
    for client in gateway_client(upstream):
        body = MESSAGES_BODY if method == "POST" else None
        response = client.request(method, path, json=body, headers=auth)

        assert response.status_code == 502
        assert response.json()["error"]["code"] == "bad_upstream_response"
        assert "script" not in response.text


def test_every_response_says_nosniff(client: TestClient) -> None:
    for response in (
        client.get("/healthz"),
        client.get("/v1/models", headers=OPENAI),
        client.post("/antifaz/scan", json={"text": "x"}, headers=OPENAI),
        client.get("/v1/models"),
    ):
        assert response.headers["x-content-type-options"] == "nosniff"


def _utf16(key: str) -> str:
    return key.encode("utf-16-le").hex()


@pytest.mark.xfail(
    strict=True,
    reason="limitation: only the whole key, raw or JSON-escaped, is detected in provider "
    "answers; a base64, UTF-16 or partial echo passes (docs/TECNICO.md, Limitaciones)",
)
@pytest.mark.parametrize(
    "echo",
    [
        lambda key: base64.b64encode(key.encode()).decode(),
        _utf16,
        lambda key: key[:-1],
    ],
    ids=["base64", "utf16-hex", "partial"],
)
def test_an_encoded_or_partial_key_echo_is_not_detected(
    upstream: FakeUpstream, echo: Callable[[str], str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": f"bad key {echo(PROVIDER_KEY)}"}})

    upstream.handler = handler
    for client in gateway_client(upstream):
        response = client.get("/v1/models", headers=OPENAI)

        assert echo(PROVIDER_KEY) not in response.text
