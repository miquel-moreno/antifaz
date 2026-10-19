"""GET /panel/api/status through the real door (issue 53, PR 11c-3b, ADR-0018).

- With a live session of the request's scheme: 200, JSON with exactly the closed schema, the
  door's security headers (no-store), and no Vary or Content-Language (the JSON has no
  language: enums only).
- Without a session, with only the API key, or with the cookie of the other scheme: 401.
  POST without the CSRF token: 403 (the door); with it: 405. HEAD: 405.
- A request from another site or port (Sec-Fetch-Site same-site or cross-site) is answered but
  never refreshes the session's last use.
- The NER state read at each request (a fake with each of the pool's states, an unknown one,
  and the NER off), providers with and without keys, default and custom base URLs.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from antifaz import __version__
from antifaz.web import sessions
from antifaz.web.headers import NO_STORE, SECURITY_HEADERS
from antifaz.web.sessions import MemorySessionStore, fingerprint
from antifaz.web.status_response import StatusResponse
from tests.integration.fakes import GATEWAY_KEY
from tests.integration.panel_client import (
    HTTPS_URL,
    LOCAL_COOKIE,
    LOCAL_URL,
    SECURE_COOKIE,
    action_headers,
    clients,
    cookie_of,
    log_in,
    logged_in,
    panel_client,
    panel_settings,
)

PATH = "/panel/api/status"
KEYS = {
    "schema",
    "version",
    "uptime_seconds",
    "panel_scheme",
    "detectors",
    "providers",
    "limits",
    "overall",
}


@pytest.fixture(params=[HTTPS_URL, LOCAL_URL])
def client(request: pytest.FixtureRequest) -> Iterator[TestClient]:
    yield from clients(request.param)


class FakeNer:
    """Only what the gateway asks of a NER outside detection: start, close and status."""

    def __init__(self, state: object) -> None:
        self.state = state

    def start(self) -> None:
        pass

    def close(self) -> None:
        pass

    def status(self) -> object:
        return self.state


def _get(client: TestClient, session_id: str | None, **headers: str) -> Any:
    if session_id is not None:
        headers["Cookie"] = f"{cookie_of(client)}={session_id}"
    return client.get(PATH, headers=headers)


def test_with_a_session_the_status_is_the_closed_json(client: TestClient) -> None:
    response = _get(client, log_in(client))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == KEYS
    StatusResponse.model_validate(body)
    assert body["schema"] == 1
    assert body["version"] == __version__
    assert body["panel_scheme"] == (
        "https" if client.base_url.scheme == "https" else "http_localhost"
    )
    assert body["detectors"] == {"validators": "on", "patterns": "on", "ner": "off"}
    assert body["limits"] == {"allowed_hosts": 5, "allowed_origins": 0, "trusted_proxies": 0}
    assert isinstance(body["uptime_seconds"], int) and body["uptime_seconds"] >= 0


def test_the_answer_has_the_door_headers_and_no_language(client: TestClient) -> None:
    response = _get(client, log_in(client))

    headers = response.headers
    assert headers["content-type"] == "application/json"
    for name, value in SECURITY_HEADERS:
        assert headers.get_list(name) == [value], name
    assert headers["cache-control"] == NO_STORE
    assert "vary" not in headers
    assert "content-language" not in headers
    assert "set-cookie" not in headers


def test_without_a_session_it_is_401(client: TestClient) -> None:
    for session_id in (None, "A" * 43, "garbage!"):
        response = _get(client, session_id)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "panel_unauthorized"
        assert response.headers["cache-control"] == NO_STORE


def test_the_api_key_never_opens_it(client: TestClient) -> None:
    for headers in (
        {"Authorization": f"Bearer {GATEWAY_KEY}"},
        {"x-api-key": GATEWAY_KEY},
    ):
        assert _get(client, None, **headers).status_code == 401


def test_the_cookie_of_the_other_scheme_is_401() -> None:
    for client in clients(HTTPS_URL):
        session_id = log_in(client)
        response = client.get(
            f"{LOCAL_URL}{PATH}", headers={"Cookie": f"{LOCAL_COOKIE}={session_id}"}
        )
        assert response.status_code == 401
        response = client.get(PATH, headers={"Cookie": f"{LOCAL_COOKIE}={session_id}"})
        assert response.status_code == 401
    for client in clients(LOCAL_URL):
        session_id = log_in(client)
        response = client.get(PATH, headers={"Cookie": f"{SECURE_COOKIE}={session_id}"})
        assert response.status_code == 401


def test_post_without_the_csrf_token_is_403_and_with_it_405(client: TestClient) -> None:
    session_id, token = logged_in(client)

    refused = client.post(PATH, content=b"{}", headers=action_headers(client, session_id, None))
    allowed = client.post(PATH, content=b"{}", headers=action_headers(client, session_id, token))

    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "csrf_rejected"
    assert allowed.status_code == 405


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_other_methods_never_get_the_status(client: TestClient, method: str) -> None:
    session_id, token = logged_in(client)
    headers = action_headers(client, session_id, token)

    assert client.request(method, PATH, content=b"{}", headers=headers).status_code == 405


def test_head_is_405(client: TestClient) -> None:
    response = client.head(PATH, headers={"Cookie": f"{cookie_of(client)}={log_in(client)}"})

    assert response.status_code == 405


# --- what counts as a use of the session ----------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Kept:
    store: MemorySessionStore
    clock = _Clock()


@pytest.fixture
def kept(monkeypatch: pytest.MonkeyPatch) -> type[_Kept]:
    _Kept.clock = _Clock()

    class KeptStore(MemorySessionStore):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(clock=_Kept.clock)
            _Kept.store = self

    # app.py imports MemorySessionStore lazily, inside _panel(): this is the one it builds.
    monkeypatch.setattr(sessions, "MemorySessionStore", KeptStore)
    return _Kept


@pytest.mark.parametrize("fetch_site", ["same-site", "cross-site", "none"])
def test_another_site_or_port_is_403_and_never_refreshes_the_last_use(
    kept: type[_Kept], fetch_site: str
) -> None:
    with panel_client(LOCAL_URL) as client:
        session_id = log_in(client)
        kept.clock.now += 600

        response = _get(client, session_id, **{"Sec-Fetch-Site": fetch_site})

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "csrf_rejected"
        assert response.headers["cache-control"] == NO_STORE
        assert kept.store._sessions[fingerprint(session_id)].last_used == 1000.0
        assert _get(client, session_id, **{"Sec-Fetch-Site": "same-origin"}).status_code == 200
        assert kept.store._sessions[fingerprint(session_id)].last_used == 1600.0


def test_only_the_panel_page_or_no_browser_reads_it(client: TestClient) -> None:
    session_id = log_in(client)

    assert _get(client, session_id).status_code == 200  # no Sec-Fetch-Site: not a browser
    assert _get(client, session_id, **{"Sec-Fetch-Site": "same-origin"}).status_code == 200
    for site in ("same-site", "cross-site", "none"):
        assert _get(client, session_id, **{"Sec-Fetch-Site": site}).status_code == 403, site
    # Without a session it is the same 403 first: nothing about the session is looked up.
    assert _get(client, None, **{"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_other_api_paths_get_the_same_rule(client: TestClient) -> None:
    cookie = {"Cookie": f"{cookie_of(client)}={log_in(client)}"}

    refused = client.get("/panel/api/nothing", headers={**cookie, "Sec-Fetch-Site": "same-site"})
    unknown = client.get("/panel/api/nothing", headers=cookie)

    assert refused.status_code == 403
    assert unknown.status_code == 404


def test_the_panel_page_keeps_its_rule_for_other_sites(client: TestClient) -> None:
    cookie = {"Cookie": f"{cookie_of(client)}={log_in(client)}"}

    for site in ("same-site", "cross-site", "none"):
        response = client.get("/panel", headers={**cookie, "Sec-Fetch-Site": site})
        assert response.status_code == 200, site


# --- the facts it reports -------------------------------------------------------------------


def _status_of(ner: object = None, **settings: Any) -> dict[str, Any]:
    with panel_client(HTTPS_URL, settings=panel_settings(**settings), ner=ner) as client:
        response = _get(client, log_in(client))
        assert response.status_code == 200
        body: dict[str, Any] = response.json()
        return body


@pytest.mark.parametrize(
    ("state", "shown", "overall"),
    [
        ("ok", "ok", "ok"),
        ("starting", "starting", "warn"),
        ("circuit_open", "circuit_open", "warn"),
        ("closed", "closed", "danger"),
        ("broken <b>", "unknown", "warn"),
        (None, "unknown", "warn"),
    ],
)
def test_each_ner_state(state: object, shown: str, overall: str) -> None:
    body = _status_of(FakeNer(state))

    assert body["detectors"]["ner"] == shown
    assert body["overall"] == overall
    assert "broken" not in str(body)


def test_the_ner_state_is_read_at_each_request() -> None:
    ner = FakeNer("starting")
    with panel_client(HTTPS_URL, ner=ner) as client:
        session_id = log_in(client)
        first = _get(client, session_id).json()["detectors"]["ner"]
        ner.state = "ok"
        second = _get(client, session_id).json()["detectors"]["ner"]

    assert (first, second) == ("starting", "ok")


def test_providers_with_and_without_keys_on_the_default_addresses() -> None:
    body = _status_of(
        openai_base_url="https://api.openai.com/v1",
        anthropic_base_url="https://api.anthropic.com",
        anthropic_api_key=None,
    )

    assert body["providers"] == [
        {"id": "openai", "has_key": True, "address": "default", "has_credentials": False},
        {"id": "anthropic", "has_key": False, "address": "default", "has_credentials": False},
    ]
    assert body["overall"] == "ok"


def test_no_provider_key_is_a_warning() -> None:
    body = _status_of(openai_api_key=None, anthropic_api_key=None)

    assert [p["has_key"] for p in body["providers"]] == [False, False]
    assert body["overall"] == "warn"


def test_custom_addresses_show_only_the_host() -> None:
    body = _status_of(
        openai_base_url="https://user:pass-not-real@proxy.example:8443/v1/path?q=1#f",
        anthropic_base_url="http://[::1]:11434",
    )

    assert body["providers"] == [
        {
            "id": "openai",
            "has_key": True,
            "address": "proxy.example:8443",
            "has_credentials": True,
        },
        {"id": "anthropic", "has_key": True, "address": "[::1]:11434", "has_credentials": False},
    ]
    assert body["overall"] == "warn"
    for fragment in ("user", "pass-not-real", "/v1", "path", "q=1", "#f"):
        assert fragment not in str(body)


class StartingNer(FakeNer):
    """Records whether the status facts were already marked started while the NER starts."""

    def __init__(self) -> None:
        super().__init__("ok")
        self.app: Any = None
        self.started_during_start: object = "not called"

    def start(self) -> None:
        self.started_during_start = self.app.state.panel_status.started


def test_the_uptime_counts_from_when_start_up_finished() -> None:
    ner = StartingNer()
    client = panel_client(HTTPS_URL, ner=ner)
    ner.app = client.app
    with client:
        facts = client.app.state.panel_status  # type: ignore[attr-defined]
        assert ner.started_during_start is None  # not yet while the NER was loading
        assert facts.started is not None


def test_the_ner_state_callable_holds_the_ner_not_the_app() -> None:
    ner = FakeNer("ok")
    with panel_client(HTTPS_URL, ner=ner) as client:
        facts = client.app.state.panel_status  # type: ignore[attr-defined]
        cells = [cell.cell_contents for cell in facts.ner_state.__closure__ or ()]

    assert ner in cells
    assert client.app not in cells


def test_the_counts_follow_the_settings() -> None:
    body = _status_of(
        allowed_origins=["https://a.example", "https://b.example"],
        trusted_proxies=["10.0.0.0/8", "192.168.0.1", "::1"],
    )

    assert body["limits"] == {"allowed_hosts": 5, "allowed_origins": 2, "trusted_proxies": 3}


def test_a_secret_free_status_has_no_key_anywhere() -> None:
    body = _status_of(openai_api_key=SecretStr("sk-not-real-openai-key-zzzzzzzzzzzz"))

    assert "sk-not-real" not in str(body)
