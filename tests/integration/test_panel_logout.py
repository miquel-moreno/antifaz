"""Getting out of the panel (issue 53, ADR-0018, PR 11b-2b), through the whole app.

- POST /panel/logout over HTTPS and over http://localhost, 127.0.0.1 and [::1]: 200, exactly one
  Set-Cookie (the expired cookie of the request's scheme) and `Clear-Site-Data: "cache"`.
- After it, the same cookie is no session (401, or 303 on GET /panel).
- The CSRF token of the page opens the logout; a new login brings a new one.
- The session store's subscribers (watch(), the hook of 11e's SSE) hear "logout" before the
  answer leaves, "expired" on the next lookup after expiry and "replaced" on a new login; a
  subscriber that fails never breaks the logout.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.types import Message

from antifaz.api.app import create_app
from antifaz.web import sessions
from antifaz.web.headers import SECURITY_HEADERS
from antifaz.web.sessions import IDLE_SECONDS, CloseReason, MemorySessionStore, fingerprint, watch
from tests.integration.panel_client import (
    ADMIN_TOKEN,
    HTTPS_URL,
    LOCAL_COOKIE,
    LOCAL_URL,
    SECURE_COOKIE,
    action_headers,
    alive,
    clients,
    cookie_of,
    csrf_of,
    logged_in,
    login_headers,
    panel_settings,
    post_login,
    post_logout,
    set_cookie,
)

LOCAL_URLS = [LOCAL_URL, "http://127.0.0.1", "http://[::1]", "http://localhost:8000"]
EXPIRED = {
    HTTPS_URL: "__Host-antifaz_session=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Strict",
    LOCAL_URL: "antifaz_session=; Max-Age=0; Path=/panel; HttpOnly; SameSite=Strict",
}


def _assert_security_headers(response: Any) -> None:
    for name, value in SECURITY_HEADERS:
        assert response.headers.get_list(name) == [value], name


def test_logout_over_https_expires_the_host_prefixed_cookie() -> None:
    for client in clients(HTTPS_URL):
        session_id, token = logged_in(client)

        response = post_logout(client, session_id, token)

        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert response.headers.get_list("set-cookie") == [EXPIRED[HTTPS_URL]]
        assert response.headers.get_list("clear-site-data") == ['"cache"']
        assert "access-control-allow-origin" not in response.headers
        _assert_security_headers(response)


@pytest.mark.parametrize("base_url", LOCAL_URLS)
def test_logout_over_http_localhost_expires_the_local_cookie(base_url: str) -> None:
    for client in clients(base_url):
        session_id, token = logged_in(client)

        response = post_logout(client, session_id, token)

        assert response.status_code == 200, response.text
        assert response.headers.get_list("set-cookie") == [EXPIRED[LOCAL_URL]]
        assert response.headers.get_list("clear-site-data") == ['"cache"']
        _assert_security_headers(response)


@pytest.mark.parametrize("base_url", [HTTPS_URL, LOCAL_URL])
def test_the_cookie_is_no_session_after_logout(base_url: str) -> None:
    for client in clients(base_url):
        session_id, token = logged_in(client)
        assert post_logout(client, session_id, token).status_code == 200

        page = client.get("/panel", headers={"Cookie": f"{cookie_of(client)}={session_id}"})
        again = post_logout(client, session_id, token)

        assert page.status_code == 303
        assert page.headers["location"] == "/panel/login"
        assert again.status_code == 401
        assert again.json()["error"]["code"] == "panel_unauthorized"
        assert "set-cookie" not in again.headers
        assert "clear-site-data" not in again.headers


@pytest.mark.parametrize(
    ("base_url", "name"), [(HTTPS_URL, SECURE_COOKIE), (LOCAL_URL, LOCAL_COOKIE)]
)
def test_the_set_cookie_of_logout_names_the_cookie_of_the_login(base_url: str, name: str) -> None:
    for client in clients(base_url):
        login = post_login(client)
        session_id = set_cookie(login)[1]
        token = csrf_of(client, session_id)

        logout = post_logout(client, session_id, token)

        assert set_cookie(login)[0] == set_cookie(logout)[0] == name
        assert set_cookie(login)[2].split("; ")[2:] == set_cookie(logout)[2].split("; ")[2:]
        assert set_cookie(logout)[1] == ""


def test_the_meta_token_opens_the_logout_and_a_new_login_brings_a_new_one() -> None:
    for client in clients(HTTPS_URL):
        first_id, first_token = logged_in(client)
        assert post_logout(client, first_id, first_token).status_code == 200

        second_id, second_token = logged_in(client)

        assert second_id != first_id
        assert second_token != first_token
        assert post_logout(client, second_id, first_token).status_code == 403  # the old token
        assert alive(client, second_id)
        assert post_logout(client, second_id, second_token).status_code == 200


def test_a_login_over_an_old_session_gives_a_new_token_and_closes_the_old_one() -> None:
    for client in clients(LOCAL_URL):
        old_id, old_token = logged_in(client)
        headers = {**login_headers(client), "Cookie": f"{LOCAL_COOKIE}={old_id}"}

        new_id = set_cookie(post_login(client, headers=headers))[1]
        new_token = csrf_of(client, new_id)

        assert not alive(client, old_id)
        assert new_token != old_token
        assert post_logout(client, old_id, old_token).status_code == 401


def test_logout_does_not_need_or_read_a_body() -> None:
    for client in clients(HTTPS_URL):
        session_id, token = logged_in(client)

        response = client.post(
            "/panel/logout", content=b"", headers=action_headers(client, session_id, token)
        )

        assert response.status_code == 200
        assert not alive(client, session_id)


def test_the_logout_route_is_registered_only_with_the_token() -> None:
    from antifaz.api.app import registered_paths

    for client in clients(HTTPS_URL):
        assert "/panel/logout" in registered_paths(client.app)  # type: ignore[arg-type]


# --- The close hook (11e's SSE will use watch()) --------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Kept:
    """The store the app made, and its fake clock."""

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


# What happened, in order: ("send", message type) from the server side, ("cut", reason) from
# a watcher. Shared by the request and the watcher so the order between them shows.
EVENTS: list[tuple[str, str]] = []


def _raw(
    app: FastAPI, method: str, path: str, headers: list[tuple[bytes, bytes]], body: bytes
) -> list[Message]:
    """One request through the whole app; the messages it sent (also logged in EVENTS)."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"localhost"), *headers],
        "client": ("127.0.0.1", 50000),
        "server": ("localhost", 80),
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        EVENTS.append(("send", message["type"]))
        sent.append(message)

    async def run() -> None:
        await asyncio.wait_for(app(scope, receive, send), timeout=10)

    asyncio.run(run())
    return sent


_CSRF = [
    (b"origin", b"http://localhost"),
    (b"sec-fetch-site", b"same-origin"),
    (b"content-type", b"application/json"),
]


def _status(sent: list[Message]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def _start_headers(sent: list[Message]) -> list[tuple[bytes, bytes]]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    return list(start["headers"])


def _login(app: FastAPI, cookie: str | None = None) -> str:
    headers = list(_CSRF)
    if cookie is not None:
        headers.append((b"cookie", f"{LOCAL_COOKIE}={cookie}".encode()))
    body = json.dumps({"token": ADMIN_TOKEN}).encode()
    sent = _raw(app, "POST", "/panel/login", headers, body)
    assert _status(sent) == 200
    value = dict(_start_headers(sent))[b"set-cookie"].decode()
    return value.split(";", 1)[0].split("=", 1)[1]


def _cut(events: list[tuple[str, str]]) -> Callable[[CloseReason], None]:
    def cut(reason: CloseReason) -> None:
        events.append(("cut", reason))

    return cut


def _logout(app: FastAPI, session_id: str, token: str) -> list[Message]:
    headers = [
        *_CSRF,
        (b"cookie", f"{LOCAL_COOKIE}={session_id}".encode()),
        (b"x-antifaz-csrf", token.encode()),
    ]
    sent = _raw(app, "POST", "/panel/logout", headers, b"{}")
    return sent


def _token_of(kept: type[_Kept], session_id: str) -> str:
    session = kept.store._sessions[fingerprint(session_id)]
    return session.csrf_token


def test_a_watcher_hears_logout_before_the_answer_leaves(kept: type[_Kept]) -> None:
    EVENTS.clear()
    app = create_app(panel_settings())
    session_id = _login(app)
    watch(kept.store, fingerprint(session_id), _cut(EVENTS))
    EVENTS.clear()

    sent = _logout(app, session_id, _token_of(kept, session_id))

    assert _status(sent) == 200
    assert EVENTS[0] == ("cut", "logout")
    assert EVENTS[1] == ("send", "http.response.start")
    assert [e for e in EVENTS if e[0] == "cut"] == [("cut", "logout")]


def test_a_watcher_hears_expired_on_the_next_lookup(kept: type[_Kept]) -> None:
    EVENTS.clear()
    app = create_app(panel_settings())
    session_id = _login(app)
    watch(kept.store, fingerprint(session_id), _cut(EVENTS))
    kept.clock.now += IDLE_SECONDS
    assert [e for e in EVENTS if e[0] == "cut"] == []

    sent = _raw(app, "GET", "/panel", [(b"cookie", f"{LOCAL_COOKIE}={session_id}".encode())], b"")

    assert _status(sent) == 303
    assert [e for e in EVENTS if e[0] == "cut"] == [("cut", "expired")]


def test_a_watcher_hears_replaced_on_a_new_login(kept: type[_Kept]) -> None:
    EVENTS.clear()
    app = create_app(panel_settings())
    old = _login(app)
    watch(kept.store, fingerprint(old), _cut(EVENTS))

    new = _login(app, cookie=old)

    assert new != old
    assert [e for e in EVENTS if e[0] == "cut"] == [("cut", "replaced")]


def test_a_failing_subscriber_never_breaks_the_logout(
    kept: type[_Kept], caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(panel_settings())
    session_id = _login(app)
    key = fingerprint(session_id)

    def broken(closed: str, reason: CloseReason) -> None:
        raise RuntimeError(f"subscriber failed for {closed} and {session_id}")

    kept.store.on_close(broken)
    caplog.set_level(logging.DEBUG)

    sent = _logout(app, session_id, _token_of(kept, session_id))

    assert _status(sent) == 200
    headers = _start_headers(sent)
    assert [v for k, v in headers if k == b"set-cookie"] == [EXPIRED[LOCAL_URL].encode()]
    assert key not in kept.store._sessions
    assert len(kept.store) == 0
    assert "session close subscriber failed: RuntimeError" in caplog.text
    for text in [caplog.text, *(str(r.__dict__) for r in caplog.records)]:
        assert key not in text
        assert session_id not in text


# --- Review fixes: what counts as a use of the session ---------------------------------------


def _last_used(kept: type[_Kept], session_id: str) -> float:
    return kept.store._sessions[fingerprint(session_id)].last_used


def _get_panel(app: FastAPI, session_id: str, fetch_site: str | None) -> int:
    headers = [(b"cookie", f"{LOCAL_COOKIE}={session_id}".encode())]
    if fetch_site is not None:
        headers.append((b"sec-fetch-site", fetch_site.encode()))
    return _status(_raw(app, "GET", "/panel", headers, b""))


def test_a_wrong_csrf_token_does_not_count_as_a_use(kept: type[_Kept]) -> None:
    app = create_app(panel_settings())
    session_id = _login(app)
    kept.clock.now += 600

    sent = _logout(app, session_id, "W" * 43)

    assert _status(sent) == 403
    assert _last_used(kept, session_id) == 1000.0


def test_a_logout_from_another_origin_does_not_count_as_a_use(kept: type[_Kept]) -> None:
    app = create_app(panel_settings())
    session_id = _login(app)
    kept.clock.now += 600
    headers = [
        (b"origin", b"http://localhost:3000"),
        (b"sec-fetch-site", b"same-site"),
        (b"content-type", b"application/json"),
        (b"cookie", f"{LOCAL_COOKIE}={session_id}".encode()),
        (b"x-antifaz-csrf", _token_of(kept, session_id).encode()),
    ]

    assert _status(_raw(app, "POST", "/panel/logout", headers, b"{}")) == 403
    assert _last_used(kept, session_id) == 1000.0


@pytest.mark.parametrize("fetch_site", ["same-site", "cross-site"])
def test_pages_of_another_site_or_port_never_keep_the_session_alive(
    kept: type[_Kept], fetch_site: str
) -> None:
    """Another localhost port is the same site: its GET carries the cookie. It is answered, but
    it does not count as a use, so the session still ends after 30 minutes without real use."""
    app = create_app(panel_settings())
    session_id = _login(app)
    for _ in range(5):
        kept.clock.now += IDLE_SECONDS / 6
        assert _get_panel(app, session_id, fetch_site) == 200
        assert _last_used(kept, session_id) == 1000.0
    kept.clock.now += IDLE_SECONDS / 6

    assert _get_panel(app, session_id, fetch_site) == 303
    assert _get_panel(app, session_id, "same-origin") == 303  # expired for everyone


@pytest.mark.parametrize("fetch_site", ["same-origin", "none", None])
def test_a_request_of_the_panel_or_without_a_browser_counts_as_a_use(
    kept: type[_Kept], fetch_site: str | None
) -> None:
    app = create_app(panel_settings())
    session_id = _login(app)
    for _ in range(5):
        kept.clock.now += IDLE_SECONDS / 2
        assert _get_panel(app, session_id, fetch_site) == 200
        assert _last_used(kept, session_id) == kept.clock.now


def test_a_head_of_another_port_does_not_count_either(kept: type[_Kept]) -> None:
    app = create_app(panel_settings())
    session_id = _login(app)
    kept.clock.now += 600
    headers = [
        (b"cookie", f"{LOCAL_COOKIE}={session_id}".encode()),
        (b"sec-fetch-site", b"same-site"),
    ]

    assert _status(_raw(app, "HEAD", "/panel", headers, b"")) == 405
    assert _last_used(kept, session_id) == 1000.0


def test_a_right_logout_closes_without_needing_a_refresh(kept: type[_Kept]) -> None:
    app = create_app(panel_settings())
    session_id = _login(app)
    kept.clock.now += IDLE_SECONDS - 1

    assert _status(_logout(app, session_id, _token_of(kept, session_id))) == 200
    assert len(kept.store) == 0


def test_a_login_over_an_expired_cookie_closes_it_as_expired(kept: type[_Kept]) -> None:
    EVENTS.clear()
    app = create_app(panel_settings())
    old = _login(app)
    watch(kept.store, fingerprint(old), _cut(EVENTS))
    kept.clock.now += IDLE_SECONDS  # expired, but nobody looked it up yet

    _login(app, cookie=old)

    assert [e for e in EVENTS if e[0] == "cut"] == [("cut", "expired")]


def test_an_https_id_presented_over_localhost_closes_it_as_scheme_mismatch(
    kept: type[_Kept],
) -> None:
    """The id of an HTTPS session, copied by hand into antifaz_session over http://localhost."""
    EVENTS.clear()
    app = create_app(panel_settings())
    session_id, session = kept.store.create("https")
    watch(kept.store, session.fingerprint, _cut(EVENTS))

    assert _get_panel(app, session_id, "same-origin") == 303
    assert [e for e in EVENTS if e[0] == "cut"] == [("cut", "scheme_mismatch")]
    assert kept.store.peek(session_id, "https") is None  # dead over HTTPS too
