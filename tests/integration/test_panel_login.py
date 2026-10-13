"""Getting into the panel (issue 53, ADR-0018, PR 11b-2a), through the whole app.

- Login over HTTPS and over http://localhost, 127.0.0.1 and [::1], each with its own cookie.
- GET /panel: 303 to the login without a session; 200 with the CSRF meta and no-store with one.
- Plain HTTP off localhost: a fixed 403 page, never a cookie (also for POST /panel/login).
- Without ANTIFAZ_ADMIN_TOKEN: every /panel* path is 404, the routes are the old ones and
  antifaz.web is never imported.
"""

import json
import subprocess
import sys

import pytest

from antifaz.api.app import registered_paths
from antifaz.web.headers import SECURITY_HEADERS
from antifaz.web.pages import HTTPS_ONLY_PAGE, LOGIN_PAGE
from tests.integration.fakes import GATEWAY_KEY, FakeUpstream
from tests.integration.panel_client import (
    HTTPS_URL,
    LAN_URL,
    LOCAL_COOKIE,
    LOCAL_URL,
    SECURE_COOKIE,
    SECURITY_HEADER_NAMES,
    clients,
    log_in,
    login_headers,
    post_login,
    set_cookie,
)
from tests.redteam.conftest import both_echo, gateway_client, gateway_settings

LOCAL_URLS = [LOCAL_URL, "http://127.0.0.1", "http://[::1]", "http://localhost:8000"]


def _assert_security_headers(response: object) -> None:
    headers = response.headers  # type: ignore[attr-defined]
    for name, value in SECURITY_HEADERS:
        assert headers.get_list(name) == [value], name


def test_login_over_https_sets_the_host_prefixed_cookie() -> None:
    for client in clients(HTTPS_URL):
        response = post_login(client)

        assert response.status_code == 200
        assert response.json() == {"ok": True}
        name, value, attributes = set_cookie(response)
        assert name == SECURE_COOKIE
        assert len(value) >= 43
        assert attributes == " Max-Age=28800; Path=/; Secure; HttpOnly; SameSite=Strict"
        _assert_security_headers(response)


@pytest.mark.parametrize("base_url", LOCAL_URLS)
def test_login_over_http_localhost_sets_the_local_cookie(base_url: str) -> None:
    for client in clients(base_url):
        response = post_login(client)

        assert response.status_code == 200, response.text
        name, _, attributes = set_cookie(response)
        assert name == LOCAL_COOKIE
        assert attributes == " Max-Age=28800; Path=/panel; HttpOnly; SameSite=Strict"


def test_get_panel_without_a_session_goes_to_the_login_page() -> None:
    for client in clients(HTTPS_URL):
        response = client.get("/panel")

        assert response.status_code == 303
        assert response.headers["location"] == "/panel/login"
        assert "set-cookie" not in response.headers
        _assert_security_headers(response)


def test_the_login_page_is_public_html() -> None:
    for client in clients(LOCAL_URL):
        response = client.get("/panel/login")

        assert response.status_code == 200
        assert response.text == LOGIN_PAGE
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert "set-cookie" not in response.headers
        _assert_security_headers(response)


@pytest.mark.parametrize(
    ("base_url", "cookie"), [(HTTPS_URL, SECURE_COOKIE), (LOCAL_URL, LOCAL_COOKIE)]
)
def test_get_panel_with_a_session_shows_the_page_with_its_csrf_meta(
    base_url: str, cookie: str
) -> None:
    for client in clients(base_url):
        session_id = log_in(client)

        response = client.get("/panel", headers={"Cookie": f"{cookie}={session_id}"})

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert response.headers["cache-control"] == "no-store"
        assert '<meta name="antifaz-csrf" content="' in response.text
        assert "set-cookie" not in response.headers
        _assert_security_headers(response)


def test_the_same_session_keeps_the_same_csrf_token() -> None:
    for client in clients(HTTPS_URL):
        cookie = {"Cookie": f"{SECURE_COOKIE}={log_in(client)}"}

        first = client.get("/panel", headers=cookie).text
        second = client.get("/panel", headers=cookie).text

        assert first == second


@pytest.mark.parametrize("path", ["/panel", "/panel/login", "/panel/x"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_plain_http_off_localhost_gets_the_fixed_https_page(path: str, method: str) -> None:
    for client in clients(LAN_URL):
        response = client.request(
            method,
            path,
            content=json.dumps({"token": "x"}).encode(),
            headers=login_headers(client),
        )

        assert response.status_code == 403
        assert response.text == HTTPS_ONLY_PAGE
        assert "set-cookie" not in response.headers
        _assert_security_headers(response)


def test_a_right_token_over_plain_http_off_localhost_still_gets_no_cookie() -> None:
    for client in clients(LAN_URL):
        response = post_login(client)

        assert response.status_code == 403
        assert response.text == HTTPS_ONLY_PAGE
        assert "set-cookie" not in response.headers


def test_an_unknown_panel_route_without_a_session_is_401() -> None:
    for client in clients(HTTPS_URL):
        response = client.get("/panel/unknown")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "panel_unauthorized"
        _assert_security_headers(response)


def test_an_unknown_panel_route_with_a_session_is_a_json_404_with_the_headers() -> None:
    for client in clients(HTTPS_URL):
        cookie = {"Cookie": f"{SECURE_COOKIE}={log_in(client)}"}

        response = client.get("/panel/unknown", headers=cookie)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
        _assert_security_headers(response)


def test_the_panel_routes_are_not_in_the_openapi_schema() -> None:
    for client in clients(HTTPS_URL):
        schema = client.app.openapi()  # type: ignore[attr-defined]

        assert not any(path.startswith("/panel") for path in schema["paths"])


def test_every_panel_answer_carries_the_headers_once() -> None:
    for client in clients(HTTPS_URL):
        for response in (client.get("/panel"), client.get("/panel/login"), post_login(client)):
            for name in SECURITY_HEADER_NAMES:
                assert len(response.headers.get_list(name)) == 1, name


# --- Without ANTIFAZ_ADMIN_TOKEN ------------------------------------------------------------

PANEL_PATHS = ["/panel", "/panel/", "/panel/login", "/panel/login/", "/panel/static/x.css"]


@pytest.mark.parametrize("path", PANEL_PATHS)
@pytest.mark.parametrize("method", ["GET", "POST", "HEAD", "OPTIONS"])
def test_without_the_token_every_panel_path_is_404_with_or_without_the_key(
    path: str, method: str
) -> None:
    for client in gateway_client(FakeUpstream(both_echo)):
        for headers in ({}, {"Authorization": f"Bearer {GATEWAY_KEY}"}):
            response = client.request(method, path, headers=headers)

            assert response.status_code == 404, (method, path)
            if method != "HEAD":
                assert response.json() == {
                    "error": {"code": "not_found", "message": "no such route"}
                }


def test_without_the_token_the_panel_404_is_byte_identical_to_an_unknown_route() -> None:
    auth = {"Authorization": f"Bearer {GATEWAY_KEY}"}
    for client in gateway_client(FakeUpstream(both_echo)):
        unknown = client.get("/v1/unknown", headers=auth)
        for headers in ({}, auth):
            panel = client.get("/panel", headers=headers)

            assert panel.status_code == unknown.status_code == 404
            assert panel.content == unknown.content
            assert panel.headers["content-type"] == unknown.headers["content-type"]


def test_without_the_token_the_routes_are_the_old_ones() -> None:
    for client in gateway_client(FakeUpstream(both_echo)):
        assert registered_paths(client.app) == {  # type: ignore[arg-type]
            "/antifaz/scan",
            "/healthz",
            "/v1/chat/completions",
            "/v1/messages",
            "/v1/messages/count_tokens",
            "/v1/models",
        }


def test_with_the_token_the_panel_routes_are_added() -> None:
    for client in clients(HTTPS_URL):
        paths = registered_paths(client.app)  # type: ignore[arg-type]

        assert {"/panel", "/panel/login"} <= paths


def test_without_the_token_antifaz_web_is_never_imported() -> None:
    code = (
        "import sys\n"
        "from pydantic import SecretStr\n"
        "from antifaz.api.app import create_app\n"
        "from antifaz.config import Settings\n"
        f"settings = Settings(antifaz_api_key=SecretStr({GATEWAY_KEY!r}), admin_token=None,\n"
        "                    _env_file=None)\n"
        "create_app(settings)\n"
        "print(sorted(m for m in sys.modules if m.startswith('antifaz.web')))\n"
    )
    result = subprocess.run(  # noqa: S603 - a fixed program, never a shell
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    )

    assert result.stdout.strip() == "[]"


def test_gateway_settings_have_no_panel() -> None:
    assert gateway_settings().admin_token is None


def test_panel_routes_reached_without_the_door_fail_closed() -> None:
    """Defence in depth: if a panel route were ever reached around the door, it answers 401
    instead of running (or of a 500)."""
    import hashlib

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from antifaz.api.errors import register_error_handlers
    from antifaz.web.door import PanelState
    from antifaz.web.login_limit import FailureLimiter
    from antifaz.web.routes import build_router
    from antifaz.web.sessions import MemorySessionStore

    state = PanelState(
        token_fingerprint=hashlib.sha256(b"x").digest(),
        trusted_proxies=(),
        sessions=MemorySessionStore(),
        limiter=FailureLimiter(),
    )
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(build_router(state))
    client = TestClient(app, base_url=LOCAL_URL)

    for method, path in (("GET", "/panel"), ("POST", "/panel/login")):
        response = client.request(method, path, json={"token": "x"})

        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "panel_unauthorized"
    assert len(state.sessions) == 0  # type: ignore[arg-type]
