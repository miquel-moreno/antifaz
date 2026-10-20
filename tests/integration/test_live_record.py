"""«En directo»: what each request records in the live feed (issue 53, PR 11e-1, ADR-0018).

Recording happens in ONE place (api/live_record.py), once per request, at its end: the routes
and the error handlers only note what they decided (antifaz.live.LiveNote). With the panel on,
the gateway keeps a MemoryLiveFeed in app.state.live_feed; with it off, a NullLiveFeed.

- Proxy routes: masked (distinct hidden values per type; allowed types such as ES_CIF never
  counted), no_data, blocked with its reason, upstream_error (the provider's 4xx/5xx, a
  timeout, a lost connection, a non-JSON answer).
- «Prueba un texto» and /antifaz/scan appear in the feed but only add to `checks`.
- A request that never reached a decision (no key, bad JSON, too large) records nothing.
- The counter never breaks the proxy: a feed that raises leaves the answer as it was, and the
  log line names only the error's type.
"""

import json
import logging
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from antifaz.api.app import SERVE_WORKERS, create_app
from antifaz.cli.serve import OPTIONS
from antifaz.detect.types import EntityType
from antifaz.live import (
    Client,
    LiveEvent,
    MemoryLiveFeed,
    NullLiveFeed,
    Origin,
    Outcome,
    Provider,
    Reason,
)
from tests.conftest import SENTINEL_DNI
from tests.integration.fakes import GATEWAY_KEY, FakeUpstream, misses_repeats, raiser
from tests.integration.panel_client import (
    HTTPS_URL,
    action_headers,
    logged_in,
    panel_client,
    panel_settings,
)
from tests.redteam.conftest import both_echo, gateway_settings

OPENAI = "/v1/chat/completions"
MESSAGES = "/v1/messages"
COUNT = "/v1/messages/count_tokens"
SCAN = "/antifaz/scan"
TRY = "/panel/api/try"
AUTH = {"Authorization": f"Bearer {GATEWAY_KEY}"}
ANTHROPIC_AUTH = {"x-api-key": GATEWAY_KEY, "anthropic-version": "2023-06-01"}
OTHER_DNI = "87654321X"
CIF = "B12345674"  # a valid company CIF: allowed by the default policy


@pytest.fixture
def client() -> Iterator[TestClient]:
    with panel_client(HTTPS_URL) as client:
        yield client


def _feed(client: TestClient) -> MemoryLiveFeed:
    feed = client.app.state.live_feed  # type: ignore[attr-defined]
    assert isinstance(feed, MemoryLiveFeed)
    return feed


def _events(client: TestClient) -> list[LiveEvent]:
    """Oldest first."""
    return list(reversed(_feed(client).snapshot().recent))


def _only(client: TestClient) -> LiveEvent:
    (event,) = _events(client)
    return event


def _openai(text: str, **extra: Any) -> dict[str, Any]:
    return {"model": "m", "messages": [{"role": "user", "content": text}], **extra}


def _anthropic(content: object, **extra: Any) -> dict[str, Any]:
    return {
        "model": "m",
        "max_tokens": 8,
        "messages": [{"role": "user", "content": content}],
        **extra,
    }


# --- The proxy -----------------------------------------------------------------------------------


def test_an_openai_request_records_its_distinct_hidden_values_per_type(client: TestClient) -> None:
    text = f"DNI {SENTINEL_DNI}, otra vez {SENTINEL_DNI}, y {OTHER_DNI}; ana@example.com; {CIF}"
    response = client.post(
        OPENAI, json=_openai(text), headers={**AUTH, "User-Agent": "OpenAI/Python 2.3.0"}
    )
    assert response.status_code == 200, response.text

    event = _only(client)
    assert event.origin is Origin.OPENAI_CHAT
    assert event.client is Client.OPENAI_PYTHON
    assert event.outcome is Outcome.MASKED
    assert event.reason is Reason.NONE
    assert event.provider is Provider.OPENAI
    assert dict(event.types) == {EntityType.ES_DNI: 2, EntityType.EMAIL: 1}  # no ES_CIF
    assert event.at % 60 == 0
    snapshot = _feed(client).snapshot()
    assert dict(snapshot.hidden) == {EntityType.ES_DNI: 2, EntityType.EMAIL: 1}
    assert snapshot.outcomes[Outcome.MASKED] == 1
    assert snapshot.checks == 0


def test_a_request_with_only_allowed_data_is_no_data(client: TestClient) -> None:
    response = client.post(OPENAI, json=_openai(f"La empresa {CIF}"), headers=AUTH)
    assert response.status_code == 200

    event = _only(client)
    assert event.outcome is Outcome.NO_DATA
    assert dict(event.types) == {}


def test_anthropic_messages_and_count_tokens_are_recorded(client: TestClient) -> None:
    headers = {**ANTHROPIC_AUTH, "User-Agent": "claude-cli/2.0.14 (external, cli)"}
    assert client.post(MESSAGES, json=_anthropic(SENTINEL_DNI), headers=headers).status_code == 200
    body = _anthropic(f"{SENTINEL_DNI} ana@example.com")
    body.pop("max_tokens")
    assert client.post(COUNT, json=body, headers=headers).status_code == 200

    messages, count = _events(client)
    assert (messages.origin, messages.provider, messages.client) == (
        Origin.ANTHROPIC_MESSAGES,
        Provider.ANTHROPIC,
        Client.CLAUDE_CODE,
    )
    assert dict(messages.types) == {EntityType.ES_DNI: 1}
    assert (count.origin, count.provider) == (Origin.COUNT_TOKENS, Provider.ANTHROPIC)
    assert dict(count.types) == {EntityType.ES_DNI: 1, EntityType.EMAIL: 1}
    assert dict(_feed(client).snapshot().hidden) == {EntityType.ES_DNI: 2, EntityType.EMAIL: 1}


def test_a_streamed_answer_is_recorded_once(client: TestClient) -> None:
    response = client.post(OPENAI, json=_openai(SENTINEL_DNI, stream=True), headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    assert _only(client).outcome is Outcome.MASKED


def test_an_attachment_is_blocked_with_its_reason(client: TestClient) -> None:
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}}
    ]
    response = client.post(MESSAGES, json=_anthropic(content), headers=ANTHROPIC_AUTH)
    assert response.status_code == 400

    event = _only(client)
    assert (event.outcome, event.reason, dict(event.types)) == (
        Outcome.BLOCKED,
        Reason.ATTACHMENT,
        {},
    )
    assert _feed(client).snapshot().outcomes[Outcome.BLOCKED] == 1


def test_a_value_left_in_clear_is_stopped_by_the_guard_and_recorded(client: TestClient) -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(FakeUpstream(both_echo)))
    app = create_app(panel_settings(), http_client=http, detector=misses_repeats)
    with TestClient(app, base_url=HTTPS_URL, raise_server_exceptions=False) as guarded:
        text = f"{SENTINEL_DNI} y {SENTINEL_DNI}"
        assert guarded.post(OPENAI, json=_openai(text), headers=AUTH).status_code == 400
        event = _only(guarded)
    assert (event.outcome, event.reason, dict(event.types)) == (
        Outcome.BLOCKED,
        Reason.EGRESS_GUARD,
        {},
    )


def test_a_failing_detector_is_recorded_as_detector_failed() -> None:
    def broken(text: str) -> list[Any]:
        raise RuntimeError(text)

    with panel_client(HTTPS_URL, detector=broken) as failing:
        assert failing.post(OPENAI, json=_openai(SENTINEL_DNI), headers=AUTH).status_code == 400
        event = _only(failing)
    assert (event.outcome, event.reason) == (Outcome.BLOCKED, Reason.DETECTOR_FAILED)


def test_an_unmaskable_field_and_a_too_deep_body_are_recorded(client: TestClient) -> None:
    thinking = {
        "role": "assistant",
        "content": [{"type": "thinking", "thinking": SENTINEL_DNI, "signature": "c2ln"}],
    }
    body = _anthropic("hola")
    body["messages"] = [{"role": "user", "content": "hola"}, thinking, *body["messages"]]
    assert client.post(MESSAGES, json=body, headers=ANTHROPIC_AUTH).status_code == 400
    deep: Any = "x"
    for _ in range(150):
        deep = [deep]
    assert (
        client.post(OPENAI, json=_openai("hola", metadata={"a": deep}), headers=AUTH).status_code
        == 400
    )

    unmaskable, too_deep = _events(client)
    assert (unmaskable.outcome, unmaskable.reason) == (Outcome.BLOCKED, Reason.UNMASKABLE_FIELD)
    assert (too_deep.outcome, too_deep.reason) == (Outcome.BLOCKED, Reason.TOO_DEEP)


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(500, json={"error": {"message": "boom"}}),
        lambda request: httpx.Response(429, json={"error": {"message": "slow down"}}),
        lambda request: httpx.Response(200, content=b"<html>not json</html>"),
        raiser(httpx.ConnectError),
        raiser(httpx.ReadTimeout),
    ],
    ids=["500", "429", "not-json", "connect-error", "timeout"],
)
def test_a_provider_failure_is_upstream_error_and_keeps_what_was_hidden(handler: Any) -> None:
    with panel_client(HTTPS_URL, upstream=FakeUpstream(handler)) as failing:
        response = failing.post(OPENAI, json=_openai(SENTINEL_DNI), headers=AUTH)
        assert response.status_code >= 400
        event = _only(failing)
        snapshot = _feed(failing).snapshot()
    assert event.outcome is Outcome.UPSTREAM_ERROR
    assert dict(event.types) == {EntityType.ES_DNI: 1}
    assert snapshot.outcomes[Outcome.UPSTREAM_ERROR] == 1
    assert dict(snapshot.hidden) == {EntityType.ES_DNI: 1}  # it was hidden before it was sent


@pytest.mark.parametrize(
    ("headers", "content"),
    [
        ({}, json.dumps(_openai(SENTINEL_DNI)).encode()),  # no key: 401
        (AUTH, b"{not json"),  # 400 before masking
        (AUTH, json.dumps(_openai("x" * (5 * 1024 * 1024))).encode()),  # 413 (4 MiB cap)
        (AUTH, json.dumps(_openai(SENTINEL_DNI, stream="yes")).encode()),  # 400
    ],
    ids=["no-key", "bad-json", "too-large", "bad-stream"],
)
def test_a_request_refused_before_any_decision_records_nothing(
    client: TestClient, headers: dict[str, str], content: bytes
) -> None:
    response = client.post(
        OPENAI, content=content, headers={**headers, "Content-Type": "application/json"}
    )
    assert response.status_code >= 400

    assert _events(client) == []


def test_other_routes_record_nothing(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200
    assert client.get("/v1/models", headers=AUTH).status_code == 200
    assert client.get("/panel/login").status_code == 200

    assert _events(client) == []


# --- «Prueba un texto» and /antifaz/scan: in the feed, not in the totals ------------------------


def test_scan_is_in_the_feed_but_not_in_the_hidden_totals(client: TestClient) -> None:
    text = f"{SENTINEL_DNI} {SENTINEL_DNI} {OTHER_DNI} {CIF}"
    assert client.post(SCAN, json={"text": text}, headers=AUTH).status_code == 200

    event = _only(client)
    assert (event.origin, event.provider, event.outcome) == (
        Origin.SCAN,
        Provider.NONE,
        Outcome.MASKED,
    )
    assert dict(event.types) == {EntityType.ES_DNI: 2}  # distinct, and never the allowed CIF
    snapshot = _feed(client).snapshot()
    assert dict(snapshot.hidden) == {}
    assert dict(snapshot.outcomes) == dict.fromkeys(Outcome, 0)
    assert snapshot.checks == 1


def test_a_failing_detector_on_scan_is_blocked_and_still_not_a_total() -> None:
    def broken(text: str) -> list[Any]:
        raise RuntimeError(text)

    with panel_client(HTTPS_URL, detector=broken) as failing:
        assert failing.post(SCAN, json={"text": SENTINEL_DNI}, headers=AUTH).status_code == 400
        event = _only(failing)
        snapshot = _feed(failing).snapshot()
    assert (event.origin, event.outcome, event.reason) == (
        Origin.SCAN,
        Outcome.BLOCKED,
        Reason.DETECTOR_FAILED,
    )
    assert snapshot.outcomes[Outcome.BLOCKED] == 0
    assert snapshot.checks == 1


def _try(client: TestClient, text: str) -> httpx.Response:
    session_id, token = logged_in(client)
    body = json.dumps({"text": text, "lang": "es"}).encode()
    return client.post(TRY, content=body, headers=action_headers(client, session_id, token))


def test_a_try_is_in_the_feed_but_not_in_the_hidden_totals(client: TestClient) -> None:
    response = _try(client, f"{SENTINEL_DNI}, {SENTINEL_DNI} y {OTHER_DNI}; {CIF}")
    assert response.status_code == 200, response.text

    event = _only(client)
    assert (event.origin, event.provider, event.outcome) == (
        Origin.TRY,
        Provider.NONE,
        Outcome.MASKED,
    )
    assert dict(event.types) == {EntityType.ES_DNI: 2}
    snapshot = _feed(client).snapshot()
    assert dict(snapshot.hidden) == {}
    assert snapshot.checks == 1


def test_a_blocked_try_is_recorded_with_its_reason() -> None:
    def broken(text: str) -> list[Any]:
        raise RuntimeError(text)

    with panel_client(HTTPS_URL, detector=broken) as failing:
        response = _try(failing, SENTINEL_DNI)
        assert response.status_code == 200
        assert response.json()["blocked_reason"] == "detector_failed"
        event = _only(failing)
    assert (event.origin, event.outcome, event.reason) == (
        Origin.TRY,
        Outcome.BLOCKED,
        Reason.DETECTOR_FAILED,
    )


def test_a_refused_try_records_nothing(client: TestClient) -> None:
    session_id, token = logged_in(client)
    bad = client.post(TRY, content=b"{}", headers=action_headers(client, session_id, token))
    assert bad.status_code == 400

    assert _events(client) == []


# --- Panel off, processes, and a counter that never breaks the proxy -----------------------------


def test_without_the_panel_the_feed_keeps_nothing() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(FakeUpstream(both_echo)))
    app = create_app(gateway_settings(), http_client=http)
    with TestClient(app) as plain:
        assert plain.post(OPENAI, json=_openai(SENTINEL_DNI), headers=AUTH).status_code == 200
        feed = plain.app.state.live_feed  # type: ignore[attr-defined]
    assert isinstance(feed, NullLiveFeed)
    assert feed.snapshot().recent == ()


def test_the_snapshot_says_how_many_processes_the_server_runs() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(FakeUpstream(both_echo)))
    app = create_app(panel_settings(), http_client=http, processes=4)
    assert app.state.live_feed.snapshot().processes == 4
    with panel_client(HTTPS_URL) as default:
        assert _feed(default).snapshot().processes == SERVE_WORKERS == OPTIONS["workers"] == 1


class _BrokenFeed(MemoryLiveFeed):
    def record(self, event: LiveEvent) -> None:
        raise RuntimeError(f"feed broke on {SENTINEL_DNI}")


def test_a_failing_feed_never_reaches_the_proxy(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    client.app.state.live_feed = _BrokenFeed()  # type: ignore[attr-defined]
    with caplog.at_level(logging.DEBUG):
        response = client.post(OPENAI, json=_openai(SENTINEL_DNI), headers=AUTH)
        assert client.post(SCAN, json={"text": SENTINEL_DNI}, headers=AUTH).status_code == 200

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == SENTINEL_DNI
    assert "RuntimeError" in caplog.text
    assert SENTINEL_DNI not in caplog.text
