"""Invariant 13 for FRAGMENTS of a configured key (issue 76).

A provider that rejects its key answers 401 (or 403) with a body that quotes part of it: the
generic prefix and the last four characters (`sk-proj-****...WxYz`). These attacks use invented
canary keys and a fake provider (httpx.MockTransport); no real provider is ever called.

- A provider 401/403 is answered with a fixed `upstream_auth_failed` (same status) on every route
  that calls a provider, streamed or not; only the status is logged, never the body.
- Any other answer (an error or a success, whole or streamed) that holds a fragment is dropped
  with the fixed 502 or the stream's fixed error event, like a whole key.
- An error that does not mention the key still passes as it is (no over-redaction).
- The «En directo» feed and the panel's «Estado» gain nothing from such an answer.
"""

import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import SecretStr

from antifaz.live import Outcome
from tests.integration.fakes import FakeUpstream, anthropic_sse, openai_sse, sse_response
from tests.integration.panel_client import HTTPS_URL, cookie_of, panel_client
from tests.redteam.conftest import gateway_client, gateway_settings

# Invented canaries (letters outside hexadecimal, no provider checksum): never real keys.
OPENAI = "sk-proj-QzCanaryOpnWvXkMtRpLsYjGhNqZuVoPiYkGxRw"
ANTHROPIC = "sk-ant-api03-QzCanaryAntWvXkMtRpLsYjGhNqZuVoPj-Qw"
GATEWAY = "QzGateCanaryVxJmPtRnLsYhGfDcBaWqUoIeKy"
ADMIN = "QzAdmnCanaryXvKpTrWjMsYgHnLqRtZuVoPiYk"
KEYS = (OPENAI, ANTHROPIC, GATEWAY, ADMIN)
MASK_CHARACTERS = "*•·…."

# The oracle's own copy of the rules (tests/unit/test_key_fragments.py checks the module's).
FRAGMENT_LENGTH = 10
SHOWN_SUFFIX = 4
# Longest first: the generic, public prefixes of the keys used here.
GENERIC_PREFIXES = ("sk-ant-admin01-", "sk-ant-api03-", "sk-svcacct-", "sk-proj-", "sk-")


def secret_part(key: str) -> str:
    """The key without its generic prefix (independent of antifaz.api.key_fragments)."""
    for prefix in GENERIC_PREFIXES:
        if key.startswith(prefix):
            return key[len(prefix) :]
    return key


OPENAI_AUTH = {"Authorization": f"Bearer {GATEWAY}"}
ANTHROPIC_AUTH = {"x-api-key": GATEWAY, "anthropic-version": "2023-06-01"}
OPENAI_BODY = {"model": "gpt-x", "messages": [{"role": "user", "content": "hola"}]}
ANTHROPIC_BODY = {
    "model": "claude-x",
    "max_tokens": 8,
    "messages": [{"role": "user", "content": "hola"}],
}


def _masked(key: str, middle: str = "*" * 20) -> str:
    """The provider-style masked key: generic prefix, a run of stars or dots, the last four."""
    prefix = key[: len(key) - len(secret_part(key))]
    return prefix + middle + key[-SHOWN_SUFFIX:]


def openai_401_body(key: str) -> dict[str, Any]:
    """OpenAI's shape for a wrong key, with the key's prefix and last four (and more pieces)."""
    message = (
        f"Incorrect API key provided: {_masked(key)}. You can find your API key at "
        f"https://platform.openai.com/account/api-keys. (also ...{key[-SHOWN_SUFFIX:]}, "
        f"{secret_part(key)[2 : 2 + FRAGMENT_LENGTH + 3]})"
    )
    return {
        "error": {
            "message": message,
            "type": "invalid_request_error",
            "param": None,
            "code": "invalid_api_key",
        }
    }


def anthropic_401_body(key: str) -> dict[str, Any]:
    """Anthropic's error shape, here quoting the key's prefix and last four."""
    message = f"invalid x-api-key {_masked(key, '•' * 8)} (key ...{key[-SHOWN_SUFFIX:]})"
    return {
        "type": "error",
        "error": {"type": "authentication_error", "message": message},
        "request_id": "req_canary_0401",
    }


# --- The oracle: independent of antifaz.api.key_fragments' matching -------------------------


def leaked(text: str, keys: tuple[str, ...] = KEYS) -> list[str]:
    """Pieces of a key in `text`: any run of FRAGMENT_LENGTH characters of a secret part, or
    the last four characters right after a star, a dot or an ellipsis."""
    found: list[str] = []
    for key in keys:
        secret = secret_part(key)
        for start in range(len(secret) - FRAGMENT_LENGTH + 1):
            run = secret[start : start + FRAGMENT_LENGTH]
            if run in text:
                found.append(run)
        last = key[-SHOWN_SUFFIX:]
        position = text.find(last)
        while position != -1:
            if position and text[position - 1] in MASK_CHARACTERS:
                found.append("..." + last)
            position = text.find(last, position + 1)
    return found


def _seen(response: httpx.Response, caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        response.text,
        str(response.headers.raw),
        caplog.text,
        *(str(record.__dict__) for record in caplog.records),
    ]


def _all_debug() -> None:
    for name in ("", "http", "uvicorn", "fastapi", "antifaz", "starlette"):
        logging.getLogger(name or None).setLevel(logging.DEBUG)


def canary_settings(**overrides: object) -> Any:
    values: dict[str, object] = {
        "antifaz_api_key": SecretStr(GATEWAY),
        "openai_api_key": SecretStr(OPENAI),
        "anthropic_api_key": SecretStr(ANTHROPIC),
        "admin_token": SecretStr(ADMIN),
        "allowed_hosts": ["testserver", "antifaz.example"],
        "log_level": "DEBUG",
    }
    values.update(overrides)
    return gateway_settings(**values)


def _client(handler: Callable[[httpx.Request], httpx.Response], **overrides: object) -> Any:
    return gateway_client(FakeUpstream(handler), canary_settings(**overrides))


# (name, method, path, request options, provider key the route uses, streamed)
Route = tuple[str, str, str, dict[str, Any], str, bool]
ROUTES: list[Route] = [
    ("openai chat", "POST", "/v1/chat/completions", {"json": OPENAI_BODY}, OPENAI, False),
    (
        "openai chat stream",
        "POST",
        "/v1/chat/completions",
        {"json": {**OPENAI_BODY, "stream": True}},
        OPENAI,
        True,
    ),
    ("anthropic messages", "POST", "/v1/messages", {"json": ANTHROPIC_BODY}, ANTHROPIC, False),
    (
        "anthropic messages stream",
        "POST",
        "/v1/messages",
        {"json": {**ANTHROPIC_BODY, "stream": True}},
        ANTHROPIC,
        True,
    ),
    (
        "anthropic count_tokens",
        "POST",
        "/v1/messages/count_tokens",
        {"json": ANTHROPIC_BODY},
        ANTHROPIC,
        False,
    ),
    ("openai models", "GET", "/v1/models", {}, OPENAI, False),
    ("anthropic models", "GET", "/v1/models", {}, ANTHROPIC, False),
]


def _request(client: TestClient, route: Route) -> httpx.Response:
    _, method, path, options, key, _ = route
    headers = OPENAI_AUTH if key == OPENAI else ANTHROPIC_AUTH
    return client.request(method, path, headers=headers, **options)


def _error_body(key: str) -> dict[str, Any]:
    return openai_401_body(key) if key == OPENAI else anthropic_401_body(key)


# --- 401 / 403: a fixed upstream_auth_failed --------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("route", ROUTES, ids=[r[0] for r in ROUTES])
def test_a_provider_auth_error_never_reaches_the_client(
    caplog: pytest.LogCaptureFixture, route: Route, status: int
) -> None:
    """The provider rejects its key and quotes a piece of it: the client gets a fixed error."""
    key = route[4]
    body = _error_body(key)
    assert leaked(json.dumps(body)), "the attack must carry fragments"
    for client in _client(lambda request: httpx.Response(status, json=body)):
        _all_debug()
        response = _request(client, route)

    assert response.status_code == status
    assert response.json()["error"]["code"] == "upstream_auth_failed"
    for text in _seen(response, caplog):
        assert leaked(text) == [], route[0]
    logged = [r.getMessage() for r in caplog.records if r.name == "antifaz.upstream"]
    assert logged and all(str(status) in message for message in logged)


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("key", [OPENAI, ANTHROPIC])
def test_an_auth_error_sent_as_an_event_stream_is_replaced_too(
    caplog: pytest.LogCaptureFixture, key: str, status: int
) -> None:
    """A provider that sends its 401 with an event-stream content type gains nothing."""
    text = "event: error\ndata: " + json.dumps(_error_body(key)) + "\n\n"
    route = next(r for r in ROUTES if r[4] == key and r[5])
    for client in _client(lambda request: sse_response(text, status=status)[0]):
        _all_debug()
        response = _request(client, route)

    assert response.status_code == status
    assert response.json()["error"]["code"] == "upstream_auth_failed"
    for seen in _seen(response, caplog):
        assert leaked(seen) == []


def test_the_auth_error_message_is_fixed_and_says_whose_key_failed() -> None:
    for client in _client(lambda r: httpx.Response(401, json=openai_401_body(OPENAI))):
        first = _request(client, ROUTES[0]).json()
    for client in _client(lambda r: httpx.Response(401, json={"error": {"message": "x"}})):
        second = _request(client, ROUTES[0]).json()

    assert first == second
    assert "provider" in first["error"]["message"]


@pytest.mark.parametrize("status", [401, 403])
def test_the_auth_error_message_does_not_blame_only_the_key(status: int) -> None:
    """A 403 may be the account's access (a model, a region), not a wrong key."""
    for client in _client(lambda r: httpx.Response(status, json={"error": {"message": "x"}})):
        message = _request(client, ROUTES[0]).json()["error"]["message"]

    assert f"HTTP {status}" in message
    assert "credentials or account access" in message
    assert "permissions" in message


# --- Other answers that hold a fragment: the fixed 502 or the stream's error event --------------


@pytest.mark.parametrize("status", [400, 404, 422, 429, 500, 503])
@pytest.mark.parametrize("route", ROUTES, ids=[r[0] for r in ROUTES])
def test_another_error_quoting_a_fragment_is_dropped_with_a_fixed_502(
    caplog: pytest.LogCaptureFixture, route: Route, status: int
) -> None:
    body = _error_body(route[4])
    for client in _client(lambda request: httpx.Response(status, json=body)):
        _all_debug()
        response = _request(client, route)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "bad_upstream_response"
    for text in _seen(response, caplog):
        assert leaked(text) == []


@pytest.mark.parametrize("route", [r for r in ROUTES if not r[5]], ids=lambda r: r[0])
def test_a_success_quoting_a_fragment_is_dropped_with_a_fixed_502(route: Route) -> None:
    piece = "sk-proj-****..." + OPENAI[-SHOWN_SUFFIX:]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"object": "list", "data": [{"id": piece}]})
        if "count_tokens" in request.url.path:
            return httpx.Response(200, json={"input_tokens": 1, "note": piece})
        if request.url.path.endswith("/chat/completions"):
            message = {"role": "assistant", "content": piece}
            return httpx.Response(200, json={"choices": [{"index": 0, "message": message}]})
        content = [{"type": "text", "text": piece}]
        return httpx.Response(
            200, json={"type": "message", "role": "assistant", "content": content}
        )

    for client in _client(handler):
        response = _request(client, route)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "bad_upstream_response"
    assert leaked(response.text) == []


def _fragment_pieces(key: str) -> list[str]:
    run = secret_part(key)[4 : 4 + FRAGMENT_LENGTH]
    return ["Tu clave ", "es ", _masked(key), " y también ", run[:5], run[5:], " fin"]


@pytest.mark.parametrize("key", [OPENAI, ANTHROPIC])
def test_a_fragment_in_the_middle_of_a_stream_ends_it_with_the_fixed_error_event(
    caplog: pytest.LogCaptureFixture, key: str
) -> None:
    """A masked key in one delta, and a run split between two: neither reaches the client."""
    pieces = _fragment_pieces(key)
    events = openai_sse(pieces) if key == OPENAI else anthropic_sse(pieces)
    route = next(r for r in ROUTES if r[4] == key and r[5])
    for client in _client(lambda request: sse_response(events, split=7)[0]):
        _all_debug()
        response = _request(client, route)

    assert response.status_code == 200
    assert "the provider's answer contained a key and was dropped" in response.text
    for text in _seen(response, caplog):
        assert leaked(text) == []


# --- No over-redaction ---------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 404, 429, 500])
@pytest.mark.parametrize("route", ROUTES, ids=[r[0] for r in ROUTES])
def test_an_error_that_does_not_mention_the_key_still_passes_as_it_is(
    route: Route, status: int
) -> None:
    body = {
        "error": {
            "type": "invalid_request_error",
            "message": "max_tokens: must be at most 8192 (sk- or sk-proj- keys alike)...",
        }
    }
    for client in _client(lambda request: httpx.Response(status, json=body)):
        response = _request(client, route)

    assert response.status_code == status
    assert response.json() == body


# --- «En directo» and «Estado» -----------------------------------------------------------------


def test_the_live_feed_records_only_an_upstream_error_for_a_provider_auth_error() -> None:
    for client in _client(lambda r: httpx.Response(401, json=openai_401_body(OPENAI))):
        response = _request(client, ROUTES[0])
        snapshot = client.app.state.live_feed.snapshot()  # type: ignore[attr-defined]

    assert response.json()["error"]["code"] == "upstream_auth_failed"
    assert snapshot.recent[0].outcome is Outcome.UPSTREAM_ERROR
    assert leaked(repr(snapshot)) == []


def test_the_panel_status_holds_no_fragment_after_a_provider_auth_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    upstream = FakeUpstream(lambda r: httpx.Response(401, json=openai_401_body(OPENAI)))
    with panel_client(HTTPS_URL, settings=canary_settings(), upstream=upstream) as client:
        client.post("/v1/chat/completions", headers=OPENAI_AUTH, json=OPENAI_BODY)
        origin = f"{client.base_url.scheme}://{client.base_url.netloc.decode('ascii')}"
        login = client.post(
            "/panel/login",
            json={"token": ADMIN},
            headers={"Sec-Fetch-Site": "same-origin", "Origin": origin},
        )
        session = login.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
        client.cookies.clear()
        status = client.get(
            "/panel/api/status", headers={"Cookie": f"{cookie_of(client)}={session}"}
        )

    assert status.status_code == 200
    for text in (status.text, str(status.headers.raw), caplog.text):
        assert leaked(text) == []


# --- Property: random configured keys --------------------------------------------------------

PREFIXES = ("", "sk-", "sk-proj-", "sk-svcacct-", "sk-ant-api03-", "sk-ant-admin01-")
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


@st.composite
def provider_keys(draw: st.DrawFn) -> str:
    prefix = draw(st.sampled_from(PREFIXES))
    body = draw(st.text(ALPHABET + "-_", min_size=24, max_size=80))
    return prefix + "Qz" + body  # "Qz": never a generic prefix nor a hexadecimal value


@st.composite
def fragments(draw: st.DrawFn, key: str) -> str:
    secret = secret_part(key)
    # Only a provider masks its own key: a key without a provider prefix gets runs only.
    kinds = ["run", "masked", "dots", "ellipsis"] if key.startswith("sk-") else ["run"]
    kind = draw(st.sampled_from(kinds))
    if kind == "run":
        size = draw(st.integers(FRAGMENT_LENGTH, len(secret)))
        start = draw(st.integers(0, len(secret) - size))
        return secret[start : start + size]
    middle = {"masked": "*" * draw(st.integers(2, 30)), "dots": "...", "ellipsis": "…"}[kind]
    return key[: len(key) - len(secret)] + middle + key[-SHOWN_SUFFIX:]


def _answer(status: int, piece: str, request: httpx.Request) -> httpx.Response:
    if status != 200:
        return httpx.Response(status, json={"error": {"message": f"bad key {piece}."}})
    if request.method == "GET":
        return httpx.Response(200, json={"object": "list", "data": [{"id": piece}]})
    if "count_tokens" in request.url.path:
        return httpx.Response(200, json={"input_tokens": 1, "x": piece})
    if request.url.path.endswith("/chat/completions"):
        message = {"role": "assistant", "content": f"ok {piece} ok"}
        return httpx.Response(200, json={"choices": [{"index": 0, "message": message}]})
    content = [{"type": "text", "text": f"ok {piece} ok"}]
    return httpx.Response(200, json={"type": "message", "role": "assistant", "content": content})


def _clients(key: str, handler: Callable[[httpx.Request], httpx.Response]) -> Iterator[Any]:
    settings_ = canary_settings(
        openai_api_key=SecretStr(key), anthropic_api_key=SecretStr(key), admin_token=None
    )
    yield from gateway_client(FakeUpstream(handler), settings_)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    data=st.data(),
    key=provider_keys(),
    status=st.sampled_from([200, 400, 401, 403, 404, 429, 500]),
    route=st.sampled_from([r for r in ROUTES if not r[5]]),
)
def test_no_response_ever_holds_a_fragment_of_a_random_configured_key(
    data: st.DataObject, key: str, status: int, route: Route
) -> None:
    piece = data.draw(fragments(key))
    _, method, path, options, provider, _ = route
    headers = OPENAI_AUTH if provider == OPENAI else ANTHROPIC_AUTH
    for client in _clients(key, lambda request: _answer(status, piece, request)):
        response = client.request(method, path, headers=headers, **options)

    assert response.status_code in (401, 403, 502)
    for text in (response.text, str(response.headers.raw)):
        assert leaked(text, (key,)) == [], (key, piece)
