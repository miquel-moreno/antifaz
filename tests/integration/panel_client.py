"""Shared pieces of the panel door tests (issue 53, ADR-0018, PR 11b-2a): settings with an
admin token, clients over HTTPS and over http://localhost, and a login helper.

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
) -> TestClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream or FakeUpstream(both_echo)))
    app = create_app(settings or panel_settings(), http_client=http)
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
