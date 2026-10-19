"""Shared pieces of the panel door tests (issue 53, ADR-0018, PRs 11b-2a and 11b-2b): settings
with an admin token, clients over HTTPS and over http://localhost, and login, CSRF token and
logout helpers.

The admin token is invented for these tests only. Cookies are always sent by hand (the client's
own cookie jar is emptied after each login), so every test says exactly which cookie travels.
"""

import json
from collections.abc import Iterator
from typing import Any

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from antifaz.api.app import create_app
from antifaz.config import Settings
from tests.integration.fakes import FakeUpstream
from tests.redteam.conftest import both_echo, gateway_settings

ADMIN_TOKEN = "test-admin-token-not-real-5c2e8a1f7b3d9e40"
HTTPS_URL = "https://antifaz.example"
LOCAL_URL = "http://localhost"
LAN_URL = "http://antifaz.example"  # plain HTTP off localhost: the panel refuses it
HOSTS = ["testserver", "localhost", "127.0.0.1", "[::1]", "antifaz.example"]
SECURE_COOKIE = "__Host-antifaz_session"
LOCAL_COOKIE = "antifaz_session"
# Every /panel* answer carries each of these exactly once. Their values are SECURITY_HEADERS
# (web/headers.py), Cache-Control "no-store" included, except a 200 of a static file of the
# manifest, whose Cache-Control is IMMUTABLE (11c-1, tests/integration/test_panel_static.py).
SECURITY_HEADER_NAMES = (
    "content-security-policy",
    "x-frame-options",
    "referrer-policy",
    "x-content-type-options",
    "cross-origin-opener-policy",
    "cross-origin-resource-policy",
    "permissions-policy",
    "cache-control",
)


def panel_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"admin_token": SecretStr(ADMIN_TOKEN), "allowed_hosts": HOSTS}
    values.update(overrides)
    return gateway_settings(**values)


def panel_client(
    base_url: str = HTTPS_URL,
    settings: Settings | None = None,
    peer: tuple[str, int] = ("testclient", 50000),
    upstream: FakeUpstream | None = None,
    ner: Any = None,
) -> TestClient:
    """`ner`: a NER detector, or a fake with start(), close() and status() (11c-3b)."""
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream or FakeUpstream(both_echo)))
    app = create_app(settings or panel_settings(), http_client=http, ner=ner)
    return TestClient(
        app,
        base_url=base_url,
        raise_server_exceptions=False,
        follow_redirects=False,
        client=peer,
    )


def clients(base_url: str = HTTPS_URL, **kwargs: Any) -> Iterator[TestClient]:
    with panel_client(base_url, **kwargs) as client:
        yield client


def origin_of(client: TestClient) -> str:
    url = client.base_url
    return f"{url.scheme}://{url.netloc.decode('ascii')}"


def login_headers(client: TestClient, **extra: str) -> dict[str, str]:
    return {
        "Sec-Fetch-Site": "same-origin",
        "Origin": origin_of(client),
        "Content-Type": "application/json",
        **extra,
    }


def post_login(
    client: TestClient,
    token: str = ADMIN_TOKEN,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
) -> httpx.Response:
    body = content if content is not None else json.dumps({"token": token}).encode()
    response = client.post("/panel/login", content=body, headers=headers or login_headers(client))
    client.cookies.clear()
    return response


def set_cookie(response: httpx.Response) -> tuple[str, str, str]:
    """(name, value, attributes) of the only Set-Cookie of a response."""
    (header,) = response.headers.get_list("set-cookie")
    pair, _, attributes = header.partition(";")
    name, _, value = pair.partition("=")
    return name, value, attributes


def log_in(client: TestClient) -> str:
    """Log in for real and return the session id the browser would hold."""
    response = post_login(client)
    assert response.status_code == 200, response.text
    return set_cookie(response)[1]


def cookie_of(client: TestClient) -> str:
    """The session cookie name of the client's scheme."""
    return SECURE_COOKIE if client.base_url.scheme == "https" else LOCAL_COOKIE


def csrf_of(client: TestClient, session_id: str) -> str:
    """The CSRF token the panel page hands to this session (its <meta>)."""
    page = client.get("/panel", headers={"Cookie": f"{cookie_of(client)}={session_id}"})
    assert page.status_code == 200, page.status_code
    return page.text.split('name="antifaz-csrf" content="', 1)[1].split('"', 1)[0]


def logged_in(client: TestClient) -> tuple[str, str]:
    """Log in for real: (the session id, its CSRF token)."""
    session_id = log_in(client)
    return session_id, csrf_of(client, session_id)


def action_headers(
    client: TestClient, session_id: str | None, token: str | None, **extra: str
) -> dict[str, str]:
    """Every CSRF layer right for a request that changes something; drop one by passing None
    (or "" in `extra` to drop a login header)."""
    headers = login_headers(client)
    if session_id is not None:
        headers["Cookie"] = f"{cookie_of(client)}={session_id}"
    if token is not None:
        headers["X-Antifaz-CSRF"] = token
    headers.update(extra)
    return {name: value for name, value in headers.items() if value}


def post_logout(
    client: TestClient,
    session_id: str | None,
    token: str | None,
    headers: dict[str, str] | list[tuple[str, str]] | None = None,
) -> httpx.Response:
    sent = headers if headers is not None else action_headers(client, session_id, token)
    response = client.post("/panel/logout", content=b"{}", headers=sent)
    client.cookies.clear()
    return response


def alive(client: TestClient, session_id: str) -> bool:
    """Whether the session still opens the panel over the client's scheme."""
    response = client.get("/panel", headers={"Cookie": f"{cookie_of(client)}={session_id}"})
    return response.status_code == 200
