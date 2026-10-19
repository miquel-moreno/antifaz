"""POST /panel/api/session and the session times of GET /panel (issue 53, PR 11d-b, ADR-0018).

The panel warns before a session expires (static/session.js). It learns the times from the page
(two metas: the seconds left before the inactivity limit and before the absolute 8 hours) and,
when the user presses «Seguir», from POST /panel/api/session:

- The door: a live session with its CSRF token, same origin, JSON (else 401 or 403); another
  site or another localhost port never reaches it, so it never extends anything. GET is 405.
- It only extends the inactivity part: the door counts the request as a use, exactly like any
  other panel request, and the answer says what is left. NEVER past the 8 hours from the login:
  near the end `idle_seconds` is capped by `max_seconds`, after it the answer is 401, and calling
  it never moves the absolute end.
- The answer: exactly {"idle_seconds", "max_seconds"} (whole seconds, never more than the server
  allows), no-store and the door's headers, the body is never read.

A fake clock drives the session store (the store's own clock, swapped before the login).
"""

import re
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from antifaz.web.headers import NO_STORE, SECURITY_HEADERS
from antifaz.web.sessions import IDLE_SECONDS, MAX_AGE_SECONDS
from tests.integration.panel_client import (
    HTTPS_URL,
    LOCAL_URL,
    action_headers,
    cookie_of,
    log_in,
    logged_in,
    panel_client,
)

SESSION = "/panel/api/session"


class Clock:
    def __init__(self, now: float = 5000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(params=[HTTPS_URL, LOCAL_URL])
def client(request: pytest.FixtureRequest) -> Iterator[TestClient]:
    with panel_client(request.param) as client:
        yield client


@pytest.fixture
def clock(client: TestClient) -> Clock:
    clock = Clock()
    client.app.state.panel_state.sessions._clock = clock  # type: ignore[attr-defined]
    return clock


def post_session(
    client: TestClient, session_id: str | None, token: str | None, **extra: str
) -> httpx.Response:
    headers = action_headers(client, session_id, token, **extra)
    return client.post(SESSION, content=b"{}", headers=headers)


def _meta(page: str, name: str) -> str:
    found = re.findall(rf'<meta name="{name}" content="([^"]*)">', page)
    assert len(found) == 1, name
    return found[0]


# --- the door ---------------------------------------------------------------------------------


def test_a_live_session_gets_its_times(client: TestClient, clock: Clock) -> None:
    session_id, token = logged_in(client)

    response = post_session(client, session_id, token)

    assert response.status_code == 200
    assert response.json() == {"idle_seconds": IDLE_SECONDS, "max_seconds": MAX_AGE_SECONDS}
    assert response.headers["cache-control"] == NO_STORE
    for name, value in SECURITY_HEADERS:
        if name != "cache-control":
            assert response.headers.get_list(name) == [value], name
    assert "set-cookie" not in response.headers


def test_without_a_session_it_is_401(client: TestClient) -> None:
    response = post_session(client, None, "x")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "panel_unauthorized"


@pytest.mark.parametrize(
    "change",
    [
        {"token": None},
        {"token": "wrong-token"},
        {"Origin": "https://evil.example"},
        {"Origin": "http://localhost:8080"},  # another localhost port
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},  # another localhost port carries the cookie
        {"Content-Type": "text/plain"},
    ],
)
def test_every_csrf_layer_is_checked_and_nothing_is_extended(
    client: TestClient, clock: Clock, change: dict[str, str | None]
) -> None:
    session_id, token = logged_in(client)
    clock.advance(IDLE_SECONDS - 60)
    extra = {k: v for k, v in change.items() if k != "token"}

    refused = post_session(client, session_id, change.get("token", token), **extra)  # type: ignore[arg-type]

    assert refused.status_code == 403, change
    assert refused.json()["error"]["code"] == "csrf_rejected"
    clock.advance(60)  # the refused request was not a use: the inactivity limit is reached
    assert post_session(client, session_id, token).status_code == 401


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_get_and_head_are_405(client: TestClient, method: str) -> None:
    session_id = log_in(client)
    headers = {"Cookie": f"{cookie_of(client)}={session_id}", "Sec-Fetch-Site": "same-origin"}

    assert client.request(method, SESSION, headers=headers).status_code == 405


def test_the_body_is_never_read(client: TestClient) -> None:
    session_id, token = logged_in(client)
    headers = action_headers(client, session_id, token)

    response = client.post(SESSION, content=b'{"idle_seconds": 99999}', headers=headers)

    assert response.json()["idle_seconds"] == IDLE_SECONDS


# --- only the inactivity part, never past the 8 hours ----------------------------------------


def test_it_extends_the_inactivity_part(client: TestClient, clock: Clock) -> None:
    session_id, token = logged_in(client)
    clock.advance(IDLE_SECONDS - 120)  # the warning: two minutes left

    extended = post_session(client, session_id, token)

    assert extended.json() == {
        "idle_seconds": IDLE_SECONDS,
        "max_seconds": MAX_AGE_SECONDS - (IDLE_SECONDS - 120),
    }
    clock.advance(IDLE_SECONDS - 1)
    assert post_session(client, session_id, token).status_code == 200


def test_it_never_moves_the_absolute_end(client: TestClient, clock: Clock) -> None:
    session_id, token = logged_in(client)
    for minutes in range(1, 10):
        clock.advance(60)
        answer = post_session(client, session_id, token).json()
        assert answer["max_seconds"] == MAX_AGE_SECONDS - 60 * minutes


def test_near_the_8_hours_the_idle_part_is_capped_by_the_end(
    client: TestClient, clock: Clock
) -> None:
    session_id, token = logged_in(client)
    while clock.now - 5000.0 < MAX_AGE_SECONDS - 300:
        clock.advance(min(IDLE_SECONDS / 2, MAX_AGE_SECONDS - 300 - (clock.now - 5000.0)))
        assert post_session(client, session_id, token).status_code == 200

    near = post_session(client, session_id, token).json()

    assert near == {"idle_seconds": 300, "max_seconds": 300}
    clock.advance(299)
    assert post_session(client, session_id, token).json() == {"idle_seconds": 1, "max_seconds": 1}
    clock.advance(1)
    assert post_session(client, session_id, token).status_code == 401  # the 8 hours are over


def test_after_the_inactivity_limit_it_is_401(client: TestClient, clock: Clock) -> None:
    session_id, token = logged_in(client)
    clock.advance(IDLE_SECONDS)

    assert post_session(client, session_id, token).status_code == 401


# --- the page says the times -------------------------------------------------------------------


def test_the_panel_page_renders_both_times(client: TestClient, clock: Clock) -> None:
    session_id = log_in(client)
    cookie = {"Cookie": f"{cookie_of(client)}={session_id}"}
    for _ in range(3):  # an hour of use, a request every 20 minutes
        clock.advance(1200)
        used = client.get("/panel/api/status", headers={**cookie, "Sec-Fetch-Site": "same-origin"})
        assert used.status_code == 200
    clock.advance(600)

    page = client.get("/panel", headers=cookie).text

    # The page itself is a use, so the inactivity part is whole again; the end is 8 h after login.
    assert _meta(page, "antifaz-session-idle") == str(IDLE_SECONDS)
    assert _meta(page, "antifaz-session-max") == str(MAX_AGE_SECONDS - 4200)


def test_near_the_end_the_page_caps_the_idle_time(client: TestClient, clock: Clock) -> None:
    session_id = log_in(client)
    cookie = {"Cookie": f"{cookie_of(client)}={session_id}"}
    while clock.now - 5000.0 < MAX_AGE_SECONDS - 90:
        clock.advance(min(IDLE_SECONDS / 2, MAX_AGE_SECONDS - 90 - (clock.now - 5000.0)))
        assert client.get("/panel", headers=cookie).status_code == 200

    page = client.get("/panel", headers=cookie).text

    assert _meta(page, "antifaz-session-idle") == "90"
    assert _meta(page, "antifaz-session-max") == "90"
