"""The panel's static files through the whole app (issue 53, ADR-0018, PR 11c-1).

- GET /panel/static/<hashed name> is public: 200 with or without a session, over HTTPS and over
  http://localhost, with the file's exact bytes and fixed Content-Type, nosniff, every other
  security header and `Cache-Control: public, max-age=31536000, immutable`.
- Only GET, and only the exact names of the manifest. Anything else follows the general rule:
  401 without a session, 404 (or 405) with one, always `no-store`. A file of the folder that is
  not in the manifest (the unhashed font, the OFL text, the plain panel.css) is never served.
- POST to a static file is a CSRF refusal (403) like any other unsafe method.
- Plain HTTP off localhost: the fixed «Solo HTTPS» page, for the static files too.
- The HTML pages keep `no-store`. Serving a file never touches the disk.
"""

from pathlib import Path
from typing import Any

import httpx
import pytest

from antifaz.web.assets import load_assets
from antifaz.web.headers import IMMUTABLE, NO_STORE, SECURITY_HEADERS
from antifaz.web.pages import HTTPS_ONLY_PAGE
from tests.integration.panel_client import (
    HTTPS_URL,
    LAN_URL,
    LOCAL_URL,
    SECURITY_HEADER_NAMES,
    action_headers,
    clients,
    cookie_of,
    log_in,
    logged_in,
)

ASSETS = load_assets()
URLS = sorted(ASSETS.urls.values())
NOT_IN_MANIFEST = [
    "/panel/static/InterVariable.woff2",
    "/panel/static/Inter-OFL.txt",
    "/panel/static/GeistMono-OFL.txt",
    "/panel/static/panel.css",
    "/panel/static/logo.svg",
    "/panel/static/__init__.py",
    "/panel/static/missing.0123456789ab.css",
]


def _static_headers_are_right(response: httpx.Response) -> None:
    for name, value in SECURITY_HEADERS:
        expected = IMMUTABLE if name == "Cache-Control" else value
        assert response.headers.get_list(name) == [expected], name
    assert response.headers["x-content-type-options"] == "nosniff"


def _no_store(response: httpx.Response) -> None:
    for name, value in SECURITY_HEADERS:
        assert response.headers.get_list(name) == [value], name
    assert response.headers["cache-control"] == NO_STORE


def _asset(url: str) -> tuple[bytes, str]:
    asset = ASSETS.files[url.removeprefix("/panel/static/")]
    return asset.body, asset.content_type


@pytest.mark.parametrize("base_url", [HTTPS_URL, LOCAL_URL, "http://127.0.0.1:8000"])
def test_every_manifest_file_is_public_with_its_bytes_type_and_cache(base_url: str) -> None:
    for client in clients(base_url):
        for url in URLS:
            response = client.get(url)

            body, content_type = _asset(url)
            assert response.status_code == 200, url
            assert response.content == body, url
            assert response.headers["content-type"] == content_type, url
            assert response.headers["content-length"] == str(len(body)), url
            assert "set-cookie" not in response.headers
            _static_headers_are_right(response)


def test_with_a_session_the_static_files_are_the_same() -> None:
    for client in clients(HTTPS_URL):
        cookie = {"Cookie": f"{cookie_of(client)}={log_in(client)}"}
        for url in URLS:
            response = client.get(url, headers=cookie)

            assert response.status_code == 200, url
            assert response.content == _asset(url)[0]
            _static_headers_are_right(response)
            assert "set-cookie" not in response.headers


def test_every_static_answer_carries_each_header_once() -> None:
    for client in clients(HTTPS_URL):
        for response in (client.get(URLS[0]), client.get(NOT_IN_MANIFEST[0])):
            for name in SECURITY_HEADER_NAMES:
                assert len(response.headers.get_list(name)) == 1, name


def test_the_html_pages_keep_no_store() -> None:
    for client in clients(HTTPS_URL):
        cookie = {"Cookie": f"{cookie_of(client)}={log_in(client)}"}
        for response in (client.get("/panel/login"), client.get("/panel", headers=cookie)):
            assert response.status_code == 200
            _no_store(response)


@pytest.mark.parametrize("url", NOT_IN_MANIFEST)
def test_a_name_outside_the_manifest_follows_the_general_rule(url: str) -> None:
    for client in clients(HTTPS_URL):
        without = client.get(url)
        with_session = client.get(url, headers={"Cookie": f"{cookie_of(client)}={log_in(client)}"})

        assert without.status_code == 401
        assert without.json()["error"]["code"] == "panel_unauthorized"
        assert with_session.status_code == 404
        assert with_session.json()["error"]["code"] == "not_found"
        for response in (without, with_session):
            _no_store(response)
            assert not any(asset.body in response.content for asset in ASSETS.files.values())


def test_the_licence_texts_ship_but_are_never_served() -> None:
    for client in clients(HTTPS_URL):
        cookie = {"Cookie": f"{cookie_of(client)}={log_in(client)}"}
        for name in ("Inter-OFL.txt", "GeistMono-OFL.txt"):
            response = client.get(f"/panel/static/{name}", headers=cookie)

            assert response.status_code == 404
            assert b"Open Font License" not in response.content


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def test_an_unsafe_method_on_a_static_file_is_a_csrf_refusal(method: str) -> None:
    for client in clients(HTTPS_URL):
        response = client.request(method, URLS[0])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "csrf_rejected"
        _no_store(response)


def test_post_to_a_static_file_with_every_csrf_layer_is_405_with_no_store() -> None:
    for client in clients(HTTPS_URL):
        session_id, token = logged_in(client)
        response = client.post(
            URLS[0], content=b"{}", headers=action_headers(client, session_id, token)
        )

        assert response.status_code == 405
        _no_store(response)


def test_head_on_a_static_file_is_not_public() -> None:
    for client in clients(HTTPS_URL):
        without = client.head(URLS[0])
        with_session = client.head(
            URLS[0], headers={"Cookie": f"{cookie_of(client)}={log_in(client)}"}
        )

        assert without.status_code == 401
        assert with_session.status_code == 405
        for response in (without, with_session):
            _no_store(response)


def test_plain_http_off_localhost_refuses_the_static_files_too() -> None:
    for client in clients(LAN_URL):
        response = client.get(URLS[0])

        assert response.status_code == 403
        assert response.text == HTTPS_ONLY_PAGE
        _no_store(response)


def test_the_pages_point_at_the_files_the_app_serves() -> None:
    for client in clients(HTTPS_URL):
        page = client.get("/panel/login").text
        for logical in ("fonts.css", "tokens.css", "panel.css", "logo.svg"):
            url = ASSETS.urls[logical]
            assert f'"{url}"' in page, logical
            assert client.get(url).status_code == 200, logical


def test_serving_a_file_never_touches_the_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    for client in clients(HTTPS_URL):
        assert client.get("/panel/login").status_code == 200  # the app is up

        def no_disk(*args: object, **kwargs: object) -> object:
            raise AssertionError("the disk was read while serving")

        monkeypatch.setattr(Path, "read_bytes", no_disk)
        monkeypatch.setattr(Path, "read_text", no_disk)
        monkeypatch.setattr(Path, "open", no_disk)
        monkeypatch.setattr(Path, "stat", no_disk)
        for url in URLS:
            response = client.get(url)

            assert response.status_code == 200, url
            assert response.content == _asset(url)[0]
        assert client.get("/panel/login").status_code == 200


def test_a_static_request_never_looks_the_session_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """A public file never reads the session, so it never keeps one alive."""
    from antifaz.web import sessions

    calls: list[str] = []
    for name in ("peek", "touch", "get"):
        real = getattr(sessions.MemorySessionStore, name)

        def spy(self: Any, *args: Any, _real: Any = real, _name: str = name) -> Any:
            calls.append(_name)
            return _real(self, *args)

        monkeypatch.setattr(sessions.MemorySessionStore, name, spy)
    for client in clients(HTTPS_URL):
        cookie = {"Cookie": f"{cookie_of(client)}={log_in(client)}"}
        calls.clear()

        assert client.get(URLS[0], headers=cookie).status_code == 200
        assert calls == []
        assert client.get(NOT_IN_MANIFEST[0], headers=cookie).status_code == 404
        assert calls == ["peek", "touch"]  # not public: the general rule


def test_a_500_inside_the_public_static_route_is_no_store_with_the_panel_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from antifaz.web.assets import Assets

    def boom(self: Assets, name: str) -> None:
        raise RuntimeError("boom")

    for client in clients(HTTPS_URL):
        monkeypatch.setattr(Assets, "get", boom)

        response = client.get(URLS[0])

        assert response.status_code == 500
        assert response.json() == {"error": {"code": "internal_error", "message": "internal error"}}
        assert "boom" not in response.text
        _no_store(response)


def test_a_bad_static_file_stops_the_app_with_the_admin_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from antifaz.api.app import create_app
    from antifaz.web import assets
    from tests.integration.panel_client import panel_settings

    (tmp_path / "x.html").write_text("<p>not a static file</p>", encoding="utf-8")
    monkeypatch.setattr(assets, "STATIC_DIR", tmp_path)
    monkeypatch.setattr(assets, "SERVED", ("x.html",))

    with pytest.raises(assets.AssetError):
        create_app(panel_settings())
