"""Attacks on the panel door (issue 53, ADR-0018, PR 11b-2a: getting in).

- Cookies: the cookie of the OTHER scheme is never a session, even when its id is valid;
  case variants, duplicates and a second cookie next to the right one.
- Paths: lookalikes stay on the gateway side; "/panel/../v1/models" in a raw ASGI scope never
  reaches the gateway route.
- CSRF on the login: Sec-Fetch-Site, Origin and the content type, refused without counting.
- Session fixation, brute force (with a spy on compare_digest), spoofed X-Forwarded-For and
  X-Forwarded-Proto, oversized bodies (chunked, without or with a false Content-Length).
- Every failed login answers the same bytes; the admin token, the session id and the CSRF
  token never reach a log, and only travel where they must.
"""

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.types import Message

from antifaz.api.app import create_app
from antifaz.web import routes
from antifaz.web.headers import SECURITY_HEADERS
from antifaz.web.pages import HTTPS_ONLY_PAGE, LOGIN_PAGE
from tests.integration.fakes import GATEWAY_KEY, FakeUpstream
from tests.integration.panel_client import (
    ADMIN_TOKEN,
    HTTPS_URL,
    LAN_URL,
    LOCAL_COOKIE,
    LOCAL_URL,
    SECURE_COOKIE,
    clients,
    log_in,
    login_headers,
    panel_client,
    panel_settings,
    post_login,
    set_cookie,
)
from tests.redteam.conftest import both_echo

LOGIN_FAILED = b'{"error":{"code":"login_failed","message":"login failed"}}'


def _assert_security_headers(headers: Any) -> None:
    """Every ADR-0018 header, once, with its value (httpx headers or raw ASGI pairs)."""
    if isinstance(headers, list):
        pairs = [(k.decode("latin-1").lower(), v.decode("latin-1")) for k, v in headers]
        for name, value in SECURITY_HEADERS:
            assert [v for k, v in pairs if k == name.lower()] == [value], name
        return
    for name, value in SECURITY_HEADERS:
        assert headers.get_list(name) == [value], name


def _login_at(client: TestClient, base: str, token: str = ADMIN_TOKEN, **headers: str) -> Any:
    sent = {
        "Sec-Fetch-Site": "same-origin",
        "Origin": base,
        "Content-Type": "application/json",
        **headers,
    }
    response = client.post(
        f"{base}/panel/login", content=json.dumps({"token": token}), headers=sent
    )
    client.cookies.clear()
    return response


def _panel(client: TestClient, base: str, cookie: str | list[str]) -> Any:
    lines = cookie if isinstance(cookie, list) else [cookie]
    return client.get(f"{base}/panel", headers=[("Cookie", line) for line in lines])


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield from clients(HTTPS_URL)


# --- Cookies by scheme ----------------------------------------------------------------------


def test_over_https_a_valid_local_cookie_is_rejected(client: TestClient) -> None:
    local_id = set_cookie(_login_at(client, LOCAL_URL))[1]

    assert _panel(client, LOCAL_URL, f"{LOCAL_COOKIE}={local_id}").status_code == 200  # valid
    assert _panel(client, HTTPS_URL, f"{LOCAL_COOKIE}={local_id}").status_code == 303


def test_over_http_localhost_a_valid_secure_cookie_is_rejected(client: TestClient) -> None:
    secure_id = set_cookie(_login_at(client, HTTPS_URL))[1]

    assert _panel(client, HTTPS_URL, f"{SECURE_COOKIE}={secure_id}").status_code == 200  # valid
    assert _panel(client, LOCAL_URL, f"{SECURE_COOKIE}={secure_id}").status_code == 303


@pytest.mark.parametrize(
    "name", ["__host-antifaz_session", "__HOST-ANTIFAZ_SESSION", "__Host-Antifaz_session"]
)
def test_case_variants_of_the_name_are_no_session(client: TestClient, name: str) -> None:
    secure_id = log_in(client)

    assert _panel(client, HTTPS_URL, f"{name}={secure_id}").status_code == 303


def test_the_right_cookie_twice_is_no_session(client: TestClient) -> None:
    secure_id = log_in(client)
    cookie = f"{SECURE_COOKIE}={secure_id}"

    assert _panel(client, HTTPS_URL, f"{cookie}; {cookie}").status_code == 303
    assert _panel(client, HTTPS_URL, [cookie, cookie]).status_code == 303
    assert _panel(client, HTTPS_URL, [cookie, f"{SECURE_COOKIE}=other"]).status_code == 303
    assert _panel(client, HTTPS_URL, cookie).status_code == 200  # once is fine


def test_a_cookie_of_another_name_next_to_the_right_one_changes_nothing(
    client: TestClient,
) -> None:
    secure_id = log_in(client)
    local_id = set_cookie(_login_at(client, LOCAL_URL))[1]

    both = f"{LOCAL_COOKIE}={local_id}; {SECURE_COOKIE}={secure_id}; theme=dark"
    assert _panel(client, HTTPS_URL, both).status_code == 200
    wrong = f"{LOCAL_COOKIE}={local_id}; {SECURE_COOKIE}=garbage"
    assert _panel(client, HTTPS_URL, wrong).status_code == 303


def test_a_made_up_or_closed_session_id_is_no_session(client: TestClient) -> None:
    assert _panel(client, HTTPS_URL, f"{SECURE_COOKIE}={'A' * 43}").status_code == 303
    old = log_in(client)
    post_login(client, headers={**login_headers(client), "Cookie": f"{SECURE_COOKIE}={old}"})
    assert _panel(client, HTTPS_URL, f"{SECURE_COOKIE}={old}").status_code == 303


# --- Paths ----------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["//panel", "/PANEL", "/panelx", "/Panel/login", "//panel/login"])
def test_lookalike_paths_stay_on_the_gateway_side(client: TestClient, path: str) -> None:
    secure_id = log_in(client)

    response = client.get(path, headers={"Cookie": f"{SECURE_COOKIE}={secure_id}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert "content-security-policy" not in response.headers


def test_the_login_path_with_a_trailing_slash_is_not_public(client: TestClient) -> None:
    for method in ("GET", "POST"):
        response = client.request(method, "/panel/login/", headers=login_headers(client))
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "panel_unauthorized"


def test_a_query_string_does_not_change_the_route(client: TestClient) -> None:
    response = client.get("/panel/login?x=1")

    assert response.status_code == 200
    assert response.text == LOGIN_PAGE


def test_the_api_key_does_not_open_the_panel(client: TestClient) -> None:
    auth = {"Authorization": f"Bearer {GATEWAY_KEY}", "x-api-key": GATEWAY_KEY}

    assert client.get("/panel", headers=auth).status_code == 303
    assert client.get("/panel/x", headers=auth).status_code == 401


def test_a_host_that_is_not_ours_is_refused_before_the_panel(client: TestClient) -> None:
    response = client.get("/panel/login", headers={"Host": "evil.example"})

    assert response.status_code == 400
    assert "set-cookie" not in response.headers


def _raw_get(app: FastAPI, path: str, cookie: str = "") -> tuple[int, bytes]:
    headers = [(b"host", b"localhost")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("localhost", 80),
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


def test_dot_segments_after_panel_never_reach_a_gateway_route() -> None:
    upstream = FakeUpstream(both_echo)
    with panel_client(LOCAL_URL, upstream=upstream) as client:
        local_id = log_in(client)
        app: FastAPI = client.app  # type: ignore[assignment]

        status, body = _raw_get(app, "/panel/../v1/models")
        assert status == 401
        assert b"panel_unauthorized" in body

        status, body = _raw_get(app, "/panel/../v1/models", f"{LOCAL_COOKIE}={local_id}")
        assert status == 404
    assert upstream.requests == []


# --- CSRF on the login ----------------------------------------------------------------------

CSRF_CASES: list[tuple[str, dict[str, str] | list[tuple[str, str]]]] = [
    ("cross-site", {"Sec-Fetch-Site": "cross-site"}),
    ("same-site", {"Sec-Fetch-Site": "same-site"}),
    ("none", {"Sec-Fetch-Site": "none"}),
    ("no Sec-Fetch-Site", {"Sec-Fetch-Site": ""}),
    ("Origin null", {"Origin": "null"}),
    ("no Origin", {"Origin": ""}),
    ("other port", {"Origin": "https://antifaz.example:8443"}),
    ("http instead of https", {"Origin": "http://antifaz.example"}),
    ("other site", {"Origin": "https://evil.example"}),
    ("text/plain", {"Content-Type": "text/plain"}),
    ("multipart", {"Content-Type": "multipart/form-data; boundary=x"}),
    ("urlencoded", {"Content-Type": "application/x-www-form-urlencoded"}),
    ("no Content-Type", {"Content-Type": ""}),
    (
        "Origin repeated",
        [
            ("Sec-Fetch-Site", "same-origin"),
            ("Content-Type", "application/json"),
            ("Origin", HTTPS_URL),
            ("Origin", HTTPS_URL),
        ],
    ),
    (
        "Sec-Fetch-Site repeated",
        [
            ("Sec-Fetch-Site", "same-origin"),
            ("Sec-Fetch-Site", "same-origin"),
            ("Content-Type", "application/json"),
            ("Origin", HTTPS_URL),
        ],
    ),
]


def _csrf_headers(
    client: TestClient, case: dict[str, str] | list[tuple[str, str]]
) -> list[tuple[str, str]]:
    if isinstance(case, list):
        return case
    merged = {**login_headers(client), **case}
    return [(name, value) for name, value in merged.items() if value]


def test_every_csrf_failure_is_403_and_does_not_count(client: TestClient) -> None:
    body = json.dumps({"token": ADMIN_TOKEN}).encode()
    for name, case in CSRF_CASES:
        response = client.post("/panel/login", content=body, headers=_csrf_headers(client, case))

        assert response.status_code == 403, name
        assert response.json()["error"]["code"] == "csrf_rejected", name
        assert "set-cookie" not in response.headers, name
    for _ in range(4):  # four real failures: with the CSRF ones counted, this would be 429
        assert post_login(client, token="wrong").status_code == 401
    assert post_login(client).status_code == 200


# --- Session fixation -----------------------------------------------------------------------


def test_logging_in_again_kills_the_old_session_and_gives_a_new_id(client: TestClient) -> None:
    old = log_in(client)
    headers = {**login_headers(client), "Cookie": f"{SECURE_COOKIE}={old}"}

    response = post_login(client, headers=headers)

    new = set_cookie(response)[1]
    assert new != old
    assert _panel(client, HTTPS_URL, f"{SECURE_COOKIE}={old}").status_code == 303
    assert _panel(client, HTTPS_URL, f"{SECURE_COOKIE}={new}").status_code == 200


def test_a_planted_unknown_id_is_never_adopted(client: TestClient) -> None:
    planted = "P" * 43
    headers = {**login_headers(client), "Cookie": f"{SECURE_COOKIE}={planted}"}

    new = set_cookie(post_login(client, headers=headers))[1]

    assert new != planted
    assert _panel(client, HTTPS_URL, f"{SECURE_COOKIE}={planted}").status_code == 303


# --- Brute force ----------------------------------------------------------------------------


def test_the_sixth_failure_is_429_and_compare_digest_is_not_called_while_blocked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    real = hmac.compare_digest

    def spy(a: Any, b: Any) -> bool:
        calls.append(1)
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    for _ in range(5):
        assert post_login(client, token="wrong").status_code == 401
    assert len(calls) == 5

    blocked = post_login(client, token="wrong")
    right = post_login(client)

    for response in (blocked, right):
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "too_many_attempts"
        assert 1 <= int(response.headers["retry-after"]) <= 60
        assert "set-cookie" not in response.headers
        _assert_security_headers(response.headers)
    assert len(calls) == 5  # never compared while blocked


def test_a_spoofed_forwarded_for_from_an_untrusted_peer_does_not_change_the_bucket() -> None:
    for client in clients(HTTPS_URL, peer=("203.0.113.9", 4000)):
        for i in range(5):
            headers = login_headers(client, **{"X-Forwarded-For": f"198.51.100.{i}"})
            assert post_login(client, token="wrong", headers=headers).status_code == 401
        headers = login_headers(client, **{"X-Forwarded-For": "198.51.100.99"})
        assert post_login(client, token="wrong", headers=headers).status_code == 429


def test_behind_a_trusted_proxy_each_client_has_its_own_bucket() -> None:
    settings = panel_settings(trusted_proxies=["10.0.0.2"])
    for client in clients(LAN_URL, settings=settings, peer=("10.0.0.2", 4000)):
        proto = {"X-Forwarded-Proto": "https", "Origin": "https://antifaz.example"}
        for _ in range(5):
            headers = login_headers(client, **proto, **{"X-Forwarded-For": "198.51.100.1"})
            assert post_login(client, token="wrong", headers=headers).status_code == 401
        headers = login_headers(client, **proto, **{"X-Forwarded-For": "198.51.100.1"})
        assert post_login(client, token="wrong", headers=headers).status_code == 429
        other = login_headers(client, **proto, **{"X-Forwarded-For": "198.51.100.2"})
        assert post_login(client, headers=other).status_code == 200


# --- X-Forwarded-Proto ----------------------------------------------------------------------


def test_https_from_a_trusted_proxy_gives_the_secure_cookie() -> None:
    settings = panel_settings(trusted_proxies=["10.0.0.2"])
    for client in clients(LAN_URL, settings=settings, peer=("10.0.0.2", 4000)):
        headers = login_headers(
            client, **{"X-Forwarded-Proto": "https", "Origin": "https://antifaz.example"}
        )

        response = post_login(client, headers=headers)

        assert response.status_code == 200
        assert set_cookie(response)[0] == SECURE_COOKIE


@pytest.mark.parametrize(
    ("peer", "proto"),
    [
        ("10.0.0.2", ["https", "https"]),
        ("10.0.0.2", ["HTTPS"]),
        ("10.0.0.2", ["https, http"]),
        ("10.0.0.3", ["https"]),  # not the trusted proxy
    ],
)
def test_a_doubtful_forwarded_proto_off_localhost_is_plain_http(
    peer: str, proto: list[str]
) -> None:
    settings = panel_settings(trusted_proxies=["10.0.0.2"])
    for client in clients(LAN_URL, settings=settings, peer=(peer, 4000)):
        body = json.dumps({"token": ADMIN_TOKEN})
        headers = [
            ("Sec-Fetch-Site", "same-origin"),
            ("Origin", "https://antifaz.example"),
            ("Content-Type", "application/json"),
            *(("X-Forwarded-Proto", value) for value in proto),
        ]

        response = client.post("/panel/login", content=body, headers=headers)

        assert response.status_code == 403
        assert response.text == HTTPS_ONLY_PAGE
        assert "set-cookie" not in response.headers


# --- Oversized and malformed bodies ---------------------------------------------------------


def _chunks(total: int, size: int = 256) -> Iterator[bytes]:
    sent = 0
    while sent < total:
        yield b" " * min(size, total - sent)
        sent += size


def test_a_body_of_exactly_1_kib_is_read_and_one_byte_more_is_refused() -> None:
    token = json.dumps({"token": ADMIN_TOKEN}).encode()
    for client in clients(HTTPS_URL):
        exact = token + b" " * (1024 - len(token))
        assert post_login(client, content=exact).status_code == 200
        response = post_login(client, content=exact + b" ")
        assert response.status_code == 401
        assert response.content == LOGIN_FAILED


def test_a_chunked_body_without_content_length_is_cut() -> None:
    for client in clients(HTTPS_URL):
        response = client.post(
            "/panel/login", content=_chunks(64 * 1024), headers=login_headers(client)
        )

        assert response.status_code == 401
        assert response.content == LOGIN_FAILED


def _raw_login(app: FastAPI, chunks: list[bytes], content_length: int | None) -> tuple[int, int]:
    """POST /panel/login through the whole app; (status, bytes the app pulled from receive)."""
    headers = [
        (b"host", b"localhost"),
        (b"origin", b"http://localhost"),
        (b"sec-fetch-site", b"same-origin"),
        (b"content-type", b"application/json"),
    ]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/panel/login",
        "raw_path": b"/panel/login",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("localhost", 80),
    }
    pulled = 0
    queue = list(chunks)
    sent: list[Message] = []

    async def receive() -> Message:
        nonlocal pulled
        if not queue:
            await asyncio.sleep(3600)  # a client that never ends: the app must stop asking
        chunk = queue.pop(0)
        pulled += len(chunk)
        return {"type": "http.request", "body": chunk, "more_body": bool(queue)}

    async def send(message: Message) -> None:
        sent.append(message)

    async def run() -> None:
        await asyncio.wait_for(app(scope, receive, send), timeout=10)

    asyncio.run(run())
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, pulled


@pytest.mark.parametrize("content_length", [None, 10, 100_000])
def test_reading_stops_at_the_cap_whatever_content_length_says(
    content_length: int | None,
) -> None:
    app = create_app(panel_settings())
    chunks = [b" " * 256] * 400  # 100 KiB on offer

    status, pulled = _raw_login(app, chunks, content_length)

    assert status == 401
    assert pulled <= 1024 + 256  # stopped at the first chunk past the cap


BAD_BODIES: list[tuple[str, bytes]] = [
    ("wrong token", json.dumps({"token": "wrong"}).encode()),
    ("token plus a letter", json.dumps({"token": ADMIN_TOKEN + "x"}).encode()),
    ("token cut", json.dumps({"token": ADMIN_TOKEN[:-1]}).encode()),
    ("empty token", b'{"token": ""}'),
    ("token not a string", b'{"token": 1}'),
    ("token null", b'{"token": null}'),
    ("token too long", json.dumps({"token": "a" * 257}).encode()),
    ("extra key", json.dumps({"token": ADMIN_TOKEN, "x": 1}).encode()),
    ("duplicate key", f'{{"token": "x", "token": "{ADMIN_TOKEN}"}}'.encode()),
    ("other key", json.dumps({"Token": ADMIN_TOKEN}).encode()),
    ("a list", json.dumps([ADMIN_TOKEN]).encode()),
    ("a string", json.dumps(ADMIN_TOKEN).encode()),
    ("not json", b"{"),
    ("empty", b""),
    ("invalid utf-8", b'{"token": "\xff\xfe"}'),
    ("lone surrogate", b'{"token": "\\ud800"}'),
    ("deep nesting", b"[" * 1000),
    ("oversized", json.dumps({"token": ADMIN_TOKEN, "pad": "x" * 2000}).encode()),
]


def test_every_failed_login_answers_the_same_bytes() -> None:
    app = create_app(panel_settings())
    answers = set()
    for i, (name, body) in enumerate(BAD_BODIES):
        client = TestClient(
            app,
            base_url=HTTPS_URL,
            client=(f"198.51.100.{i + 1}", 1),
            raise_server_exceptions=False,
        )
        response = client.post("/panel/login", content=body, headers=login_headers(client))

        assert response.status_code == 401, name
        assert response.content == LOGIN_FAILED, name
        assert "set-cookie" not in response.headers, name
        headers = {k: v for k, v in response.headers.items() if k != "x-request-id"}
        answers.add((response.content, tuple(sorted(headers.items()))))
    assert len(answers) == 1


def test_bad_bodies_count_as_failures() -> None:
    for client in clients(HTTPS_URL):
        for _, body in BAD_BODIES[:5]:
            assert post_login(client, content=body).status_code == 401
        assert post_login(client).status_code == 429


# --- Canaries: the admin token, the session id and the CSRF token ---------------------------

ADMIN_CANARY = "canary-admin-token-not-real-3b9f1d7e5c2a8f60"


def test_secrets_never_reach_a_log_and_only_travel_where_they_must(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = panel_settings(admin_token=SecretStr(ADMIN_CANARY), log_level="DEBUG")
    with panel_client(HTTPS_URL, settings=settings) as client:
        for name in ("", "http", "uvicorn", "fastapi", "antifaz", "starlette"):
            logging.getLogger(name or None).setLevel(logging.DEBUG)
        responses = [client.get("/panel/login"), client.get("/panel")]
        responses.append(post_login(client, token=ADMIN_CANARY[:-1]))
        responses.append(post_login(client, token=ADMIN_CANARY[::-1]))
        first = post_login(client, token=ADMIN_CANARY)
        session_id = set_cookie(first)[1]
        cookie = {"Cookie": f"{SECURE_COOKIE}={session_id}"}
        page = client.get("/panel", headers=cookie)
        csrf = page.text.split('name="antifaz-csrf" content="', 1)[1].split('"', 1)[0]
        responses.append(client.get("/panel/unknown", headers=cookie))
        responses.append(client.get("/panel/login/", headers=cookie))
        again = post_login(client, token=ADMIN_CANARY, headers={**login_headers(client), **cookie})
        responses.append(client.get("/panel", headers=cookie))  # the old session is gone

    assert caplog.records, "nothing was logged: the check would be empty"
    logs = [caplog.text, *(str(record.__dict__) for record in caplog.records)]
    for text in logs:
        for secret in (
            ADMIN_CANARY,
            session_id,
            csrf,
            hashlib.sha256(ADMIN_CANARY.encode()).hexdigest(),
        ):
            assert secret not in text
    for response in [*responses, first, page, again]:
        assert ADMIN_CANARY not in response.text
        assert ADMIN_CANARY not in str(response.headers.raw)
    # The session id only in the Set-Cookie of its own login; the CSRF token only in the meta.
    for response in [*responses, page, again]:
        assert session_id not in response.text
        assert session_id not in str(response.headers.raw)
    assert session_id not in first.text
    for response in [*responses, first, again]:
        assert csrf not in response.text
        assert csrf not in str(response.headers.raw)
    assert page.text.count(csrf) == 1
    assert csrf not in str(page.headers.raw)


# --- Review fixes: disconnects, slow bodies, root_path, 500s, shared counters ---------------


def _run_raw(
    app: FastAPI,
    method: str,
    path: str,
    headers: list[tuple[bytes, bytes]],
    receive: Any,
    root_path: str = "",
) -> list[Message]:
    """One request through the whole app with a hand-made scope; the messages it sent."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": root_path,
        "headers": [(b"host", b"localhost"), *headers],
        "client": ("127.0.0.1", 50000),
        "server": ("localhost", 80),
    }
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async def run() -> None:
        # An error is what the server would log; the test checks what was sent.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(app(scope, receive, send), timeout=10)

    asyncio.run(run())
    return sent


def _status(sent: list[Message]) -> int | None:
    starts = [m["status"] for m in sent if m["type"] == "http.response.start"]
    return starts[0] if starts else None


LOCAL_LOGIN_HEADERS = [
    (b"origin", b"http://localhost"),
    (b"sec-fetch-site", b"same-origin"),
    (b"content-type", b"application/json"),
]


def _right_login(app: FastAPI) -> int | None:
    body = json.dumps({"token": ADMIN_TOKEN}).encode()

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    return _status(_run_raw(app, "POST", "/panel/login", LOCAL_LOGIN_HEADERS, receive))


def _queue_receive(messages: list[Message], then_wait: bool) -> Any:
    async def receive() -> Message:
        if messages:
            return messages.pop(0)
        if then_wait:
            await asyncio.sleep(3600)  # a slow client: the next piece never comes
        return {"type": "http.disconnect"}

    return receive


def test_a_client_that_disconnects_mid_body_gets_no_500_and_does_not_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(panel_settings())
    for _ in range(6):  # more than the limit: if counted, the right login below would be 429
        messages: list[Message] = [
            {"type": "http.request", "body": b'{"tok', "more_body": True},
            {"type": "http.disconnect"},
        ]
        receive = _queue_receive(messages, then_wait=False)

        sent = _run_raw(app, "POST", "/panel/login", LOCAL_LOGIN_HEADERS, receive)

        assert _status(sent) != 500

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert _right_login(app) == 200


def test_a_body_that_never_ends_is_cut_by_the_deadline_without_counting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "LOGIN_BODY_SECONDS", 0.05)
    app = create_app(panel_settings())
    for _ in range(6):
        messages: list[Message] = [
            {"type": "http.request", "body": b'{"token": "', "more_body": True}
        ]
        receive = _queue_receive(messages, then_wait=True)

        sent = _run_raw(app, "POST", "/panel/login", LOCAL_LOGIN_HEADERS, receive)

        assert _status(sent) == 401
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        assert body == LOGIN_FAILED
        start = next(m for m in sent if m["type"] == "http.response.start")
        _assert_security_headers(start["headers"])

    assert _right_login(app) == 200  # six slow bodies did not count


def test_the_body_deadline_is_five_seconds() -> None:
    assert routes.LOGIN_BODY_SECONDS == 5


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/x/panel", 303),
        ("GET", "/x/panel/login", 200),
        ("POST", "/x/panel/login", 403),  # CSRF: the API key is no login
        ("GET", "/x/panel/other", 401),
    ],
)
def test_under_a_root_path_the_panel_still_goes_through_its_door(
    method: str, path: str, expected: int
) -> None:
    app = create_app(panel_settings())
    auth = [(b"authorization", f"Bearer {GATEWAY_KEY}".encode())]

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = _run_raw(app, method, path, auth, receive, root_path="/x")

    assert _status(sent) == expected
    start = next(m for m in sent if m["type"] == "http.response.start")
    _assert_security_headers(start["headers"])  # the door answered, not the router alone


def _boom(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError("boom")


@pytest.mark.parametrize("target", ["panel_page", "_token"])
def test_a_500_inside_the_panel_carries_the_panel_headers(
    monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    for client in clients(HTTPS_URL):
        cookie = {"Cookie": f"{SECURE_COOKIE}={log_in(client)}"}
        monkeypatch.setattr(routes, target, _boom)

        if target == "panel_page":
            response = client.get("/panel", headers=cookie)
        else:
            response = post_login(client)

        assert response.status_code == 500
        assert response.json() == {"error": {"code": "internal_error", "message": "internal error"}}
        _assert_security_headers(response.headers)
        assert "boom" not in response.text


def test_put_on_the_panel_with_a_session_is_405_with_the_headers(client: TestClient) -> None:
    cookie = {"Cookie": f"{SECURE_COOKIE}={log_in(client)}"}

    response = client.put("/panel", headers=cookie)

    assert response.status_code == 405
    _assert_security_headers(response.headers)


def test_behind_a_trusted_proxy_without_forwarded_for_every_client_shares_one_counter() -> None:
    """Documented limit: a trusted proxy that does not add X-Forwarded-For makes every client
    look like the proxy, so five failures from anyone lock everyone out for a minute."""
    settings = panel_settings(trusted_proxies=["10.0.0.2"])
    for client in clients(LAN_URL, settings=settings, peer=("10.0.0.2", 4000)):
        proto = {"X-Forwarded-Proto": "https", "Origin": "https://antifaz.example"}
        for _ in range(5):
            headers = login_headers(client, **proto)
            assert post_login(client, token="wrong", headers=headers).status_code == 401
        assert post_login(client, headers=login_headers(client, **proto)).status_code == 429
