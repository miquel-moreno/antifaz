"""POST /panel/api/status/check through the real door (issue 53, PR 11e-4, ADR-0018).

«Comprobar ahora»: the gateway lists the models of each provider with a key (free, nothing is
generated) on its OWN client (app.state.http_client), and answers with the closed CheckResponse:
one closed value per provider (the HTTP code only for refused, redirect and unexpected) and a
fresh local status snapshot. In this order:

- The door: a live session of the request's scheme, its CSRF token and same origin + JSON; the
  press counts as a use of the session. The body is never read. GET and HEAD are 405.
- The gate (web/check_gate.py), taken before anything awaits: one check per process (another at
  the same time is 503 check_busy with Retry-After: 1) and a cooldown of 30 s from the end of the
  last one (429 check_cooldown with Retry-After, the whole seconds left rounded up).
- Any failure inside is a fixed 500 and one log line with the error's type; the gate is given
  back all the same.
- One INFO log line per check with only the closed values, e.g. `openai=ok anthropic=refused`.

GET /panel renders the header pill and the Estado tab's dot from the LOCAL overall (no provider
call): a failed provider check never changes it.

The providers are a MockTransport (FakeUpstream); the socket guard refuses any other connect.
"""

import logging
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from antifaz.api.provider_check import model_list_request
from antifaz.web import sessions
from antifaz.web.headers import NO_STORE, SECURITY_HEADERS
from antifaz.web.sessions import MemorySessionStore, fingerprint
from antifaz.web.status_response import CheckResponse
from tests.conftest import forbid_network
from tests.integration.fakes import GATEWAY_KEY, FakeUpstream, models_list
from tests.integration.panel_client import (
    HTTPS_URL,
    LOCAL_URL,
    action_headers,
    cookie_of,
    log_in,
    logged_in,
    panel_client,
    panel_settings,
)

PATH = "/panel/api/status/check"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_network(monkeypatch)


class _Clock:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream(models_list)


@pytest.fixture(params=[HTTPS_URL, LOCAL_URL])
def client(request: pytest.FixtureRequest, upstream: FakeUpstream) -> Iterator[TestClient]:
    with panel_client(request.param, upstream=upstream) as test_client:
        yield test_client


def _gate(client: TestClient) -> Any:
    return client.app.state.panel_state.check_gate  # type: ignore[attr-defined]


def _clocked(client: TestClient) -> _Clock:
    clock = _Clock()
    _gate(client).clock = clock
    return clock


def _press(client: TestClient, session_id: str, token: str, body: bytes = b"{}") -> httpx.Response:
    return client.post(PATH, content=body, headers=action_headers(client, session_id, token))


def test_a_press_lists_the_models_of_both_and_answers_the_closed_json(
    client: TestClient, upstream: FakeUpstream
) -> None:
    session_id, token = logged_in(client)
    upstream.requests.clear()

    response = _press(client, session_id, token)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"schema", "providers", "status"}
    CheckResponse.model_validate(body)
    assert body["schema"] == 1
    assert body["providers"] == [
        {"id": "openai", "result": "ok", "http_status": None},
        {"id": "anthropic", "result": "ok", "http_status": None},
    ]
    assert body["status"]["overall"] == "ok"
    assert body["status"]["schema"] == 1
    settings = client.app.state.settings  # type: ignore[attr-defined]
    expected = sorted(model_list_request(p, settings)[0] for p in ("openai", "anthropic"))
    assert sorted(str(r.url) for r in upstream.requests) == expected
    assert all(r.method == "GET" for r in upstream.requests)


def test_the_answer_has_the_door_headers_and_no_language(client: TestClient) -> None:
    session_id, token = logged_in(client)

    response = _press(client, session_id, token)

    assert response.headers["content-type"] == "application/json"
    for name, value in SECURITY_HEADERS:
        assert response.headers.get_list(name) == [value], name
    assert response.headers["cache-control"] == NO_STORE
    assert "vary" not in response.headers
    assert "content-language" not in response.headers
    assert "set-cookie" not in response.headers


def test_the_body_is_never_read(client: TestClient) -> None:
    session_id, token = logged_in(client)

    response = _press(client, session_id, token, body=b"not json at all {")

    assert response.status_code == 200


def test_without_a_session_or_with_only_the_api_key_it_is_401(
    client: TestClient, upstream: FakeUpstream
) -> None:
    log_in(client)
    upstream.requests.clear()
    headers = action_headers(client, None, None)
    for extra in ({}, {"Authorization": f"Bearer {GATEWAY_KEY}"}, {"x-api-key": GATEWAY_KEY}):
        response = client.post(PATH, content=b"{}", headers={**headers, **extra})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "panel_unauthorized"
    assert upstream.requests == []


def test_without_the_csrf_token_or_from_another_site_it_is_403(
    client: TestClient, upstream: FakeUpstream
) -> None:
    session_id, token = logged_in(client)
    upstream.requests.clear()

    no_token = client.post(PATH, content=b"{}", headers=action_headers(client, session_id, None))
    other = client.post(
        PATH,
        content=b"{}",
        headers=action_headers(client, session_id, token, **{"Sec-Fetch-Site": "same-site"}),
    )
    form = client.post(
        PATH,
        content=b"{}",
        headers=action_headers(client, session_id, token, **{"Content-Type": "text/plain"}),
    )

    for response in (no_token, other, form):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "csrf_rejected"
    assert upstream.requests == []


def test_get_and_head_are_405(client: TestClient, upstream: FakeUpstream) -> None:
    cookie = {"Cookie": f"{cookie_of(client)}={log_in(client)}"}
    upstream.requests.clear()

    assert client.get(PATH, headers=cookie).status_code == 405
    assert client.head(PATH, headers=cookie).status_code == 405
    assert upstream.requests == []


def test_a_second_press_within_30_s_of_the_end_is_429_with_the_seconds_left(
    client: TestClient, upstream: FakeUpstream
) -> None:
    clock = _clocked(client)
    session_id, token = logged_in(client)
    assert _press(client, session_id, token).status_code == 200
    upstream.requests.clear()
    clock.now += 10.4

    response = _press(client, session_id, token)

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "check_cooldown"
    assert response.headers["retry-after"] == "20"
    assert upstream.requests == []
    clock.now += 19.6
    assert _press(client, session_id, token).status_code == 200


def test_a_press_while_a_check_runs_is_503_check_busy(
    client: TestClient, upstream: FakeUpstream
) -> None:
    session_id, token = logged_in(client)
    gate = _gate(client)
    assert gate.begin(gate.now()) is None  # a check is running
    upstream.requests.clear()

    response = _press(client, session_id, token)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "check_busy"
    assert response.headers["retry-after"] == "1"
    assert upstream.requests == []


def test_a_failure_inside_is_a_fixed_500_logged_by_type_and_the_gate_is_given_back(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    clock = _clocked(client)
    session_id, token = logged_in(client)
    state = client.app.state  # type: ignore[attr-defined]

    async def broken() -> list[dict[str, Any]]:
        raise RuntimeError("secret detail 12345678Z")

    state.provider_check.run = broken

    response = _press(client, session_id, token)

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "internal error"}}
    assert "secret detail" not in caplog.text
    assert "12345678Z" not in caplog.text
    assert [r.getMessage() for r in caplog.records if "check failed" in r.getMessage()] == [
        "panel status check failed: RuntimeError"
    ]
    assert not _gate(client).running
    clock.now += 30
    del state.provider_check.run
    assert _press(client, session_id, token).status_code == 200


def test_one_info_line_per_check_with_only_the_closed_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def refuse_anthropic(request: httpx.Request) -> httpx.Response:
        if "anthropic-version" in request.headers:
            return httpx.Response(401, text="Incorrect key, the HTTP status was 401")
        return models_list(request)

    caplog.set_level(logging.DEBUG)
    with panel_client(HTTPS_URL, upstream=FakeUpstream(refuse_anthropic)) as client:
        session_id, token = logged_in(client)
        caplog.clear()
        response = _press(client, session_id, token)

    assert response.json()["providers"][1] == {
        "id": "anthropic",
        "result": "refused",
        "http_status": 401,
    }
    lines = [r for r in caplog.records if r.name.startswith("antifaz")]
    checks = [r for r in lines if "openai=" in r.getMessage()]
    assert [(r.levelno, r.getMessage()) for r in checks] == [
        (logging.INFO, "provider check: openai=ok anthropic=refused")
    ]
    # The messages, not caplog.text: its format carries the source line number.
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "401" not in messages
    assert "Incorrect" not in messages


def test_the_status_in_the_answer_is_a_fresh_local_snapshot(client: TestClient) -> None:
    session_id, token = logged_in(client)
    status = client.get(
        PATH.rsplit("/", 1)[0], headers={"Cookie": f"{cookie_of(client)}={session_id}"}
    )

    response = _press(client, session_id, token)

    fresh = dict(response.json()["status"])
    before = dict(status.json())
    fresh.pop("uptime_seconds")
    before.pop("uptime_seconds")
    assert fresh == before


def test_a_failed_provider_check_never_changes_the_local_overall() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    with panel_client(HTTPS_URL, upstream=FakeUpstream(refuse)) as client:
        session_id, token = logged_in(client)
        response = _press(client, session_id, token)
        page = client.get("/panel", headers={"Cookie": f"{cookie_of(client)}={session_id}"})

    assert [p["result"] for p in response.json()["providers"]] == ["refused", "refused"]
    assert response.json()["status"]["overall"] == "ok"
    assert 'data-app-status data-overall="ok"' in page.text


# --- what counts as a use of the session ----------------------------------------------------


def test_the_press_counts_as_a_use_of_the_session(
    monkeypatch: pytest.MonkeyPatch, upstream: FakeUpstream
) -> None:
    clock = _Clock()
    kept: dict[str, MemorySessionStore] = {}

    class KeptStore(MemorySessionStore):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(clock=clock)
            kept["store"] = self

    monkeypatch.setattr(sessions, "MemorySessionStore", KeptStore)
    with panel_client(LOCAL_URL, upstream=upstream) as client:
        session_id, token = logged_in(client)
        clock.now += 600

        assert _press(client, session_id, token).status_code == 200

        assert kept["store"]._sessions[fingerprint(session_id)].last_used == clock.now


# --- GET /panel: the pill and the dot from the local overall ----------------------------------


@pytest.mark.parametrize(
    ("settings", "overall"),
    [
        ({}, "ok"),
        ({"openai_api_key": None, "anthropic_api_key": None}, "danger"),
        ({"openai_base_url": "https://user:pw@proxy.check.invalid/v1"}, "warn"),
    ],
)
def test_the_panel_page_renders_the_pill_and_the_dot_from_the_local_overall(
    settings: dict[str, Any], overall: str, upstream: FakeUpstream
) -> None:
    with panel_client(HTTPS_URL, settings=panel_settings(**settings), upstream=upstream) as client:
        upstream.requests.clear()
        session_id = log_in(client)
        page = client.get("/panel", headers={"Cookie": f"{cookie_of(client)}={session_id}"})

    assert page.status_code == 200
    assert page.text.count(f'data-app-status data-overall="{overall}"') == 2  # header and phone bar
    dot = page.text.split('id="status-dot"', 1)[1].split(">", 1)[0]
    assert ("hidden" in dot) is (overall == "ok")
    assert upstream.requests == []  # rendering the page never calls a provider
