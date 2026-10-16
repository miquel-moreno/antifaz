"""Attacks on the panel's static files (issue 53, ADR-0018, PR 11c-1).

The public rule is "GET and the exact path of a manifest file", and the route looks the name up
in a dict held in memory: no path is ever joined to a folder. These requests go in as raw ASGI
scopes (no client normalises them first), with and without a live session, and none may answer
200 or carry the bytes of any file: dot segments, encoded and double-encoded slashes and dots,
backslashes, fullwidth characters, NUL, case changes, doubled or trailing slashes, ;params, the
plain or upper-case name, another file's hash, Python sources and templates. The served SVGs
hold nothing that runs.
"""

import asyncio
import re
from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from starlette.types import Message

from antifaz.web.assets import load_assets
from antifaz.web.headers import IMMUTABLE
from tests.integration.panel_client import (
    HTTPS_URL,
    LOCAL_COOKIE,
    LOCAL_URL,
    clients,
    log_in,
    panel_client,
)

ASSETS = load_assets()
P = "/panel/static/"
CSS = ASSETS.urls["panel.css"].removeprefix(P)  # panel.<hash>.css
TOKENS = ASSETS.urls["tokens.css"].removeprefix(P)
FONT = ASSETS.urls["InterVariable.woff2"].removeprefix(P)
OTHER_HASH = "panel." + TOKENS.split(".")[1] + ".css"  # the right stem with another file's hash

TRICKS = [
    P + "../static/" + CSS,
    P + "./" + CSS,
    P + "%2e%2e%2fstatic%2f" + CSS,
    P + "..%2fstatic%2f" + CSS,
    P + "%2F" + CSS,
    P + "%2f" + CSS,
    P + "..\\static\\" + CSS,
    P + "%5c" + CSS,
    P + "%5C..%5C" + CSS,
    P + "%252e%252e%252f" + CSS,
    P + "%25" + CSS,
    P + CSS.replace(".", "%2e"),
    P + CSS.replace(".", "\uff0e"),  # fullwidth full stop
    P + "\uff0e\uff0e\uff0f" + CSS,  # fullwidth ../
    P + CSS + "%00",
    P + CSS + "\x00",
    P + CSS + "\x00.txt",
    "/PANEL/static/" + CSS,
    "/Panel/Static/" + CSS,
    "/panel/STATIC/" + CSS,
    "//panel/static/" + CSS,
    "/panel//static/" + CSS,
    P + "/" + CSS,
    P + CSS + "/",
    P + CSS + "//",
    P + CSS + ";x=1",
    P + CSS + ";",
    P + CSS + "#x",
    P + CSS + " ",
    P + " " + CSS,
    P + CSS.upper(),
    P + "panel.css",
    P + OTHER_HASH,
    P + FONT.replace(".woff2", ".WOFF2"),
    P + "InterVariable.woff2",
    P + "Inter-OFL.txt",
    P + "__init__.py",
    P + "../__init__.py",
    P + "../pages.py",
    P + "../templates/base.html",
    P + "templates/base.html",
    "/panel/templates/base.html",
    "/panel/static",
    P,
    P + ".",
    P + "..",
    "/panel/static/" + CSS.replace("panel.", "../web/static/panel."),
]


def _raw_get(app: FastAPI, path: str, cookie: str = "", method: str = "GET") -> tuple[int, bytes]:
    headers = [(b"host", b"localhost")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8", "surrogatepass"),
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


@pytest.fixture(scope="module")
def app_and_cookie() -> Iterator[tuple[FastAPI, str]]:
    with panel_client(LOCAL_URL) as client:
        app: FastAPI = client.app  # type: ignore[assignment]
        yield app, f"{LOCAL_COOKIE}={log_in(client)}"


def _carries_a_file(body: bytes) -> bool:
    return any(asset.body in body for asset in ASSETS.files.values())


def test_the_real_name_answers_so_the_tricks_below_are_meaningful(
    app_and_cookie: tuple[FastAPI, str],
) -> None:
    app, cookie = app_and_cookie
    for sent_cookie in ("", cookie):
        status, body = _raw_get(app, P + CSS, sent_cookie)

        assert status == 200
        assert body == ASSETS.files[CSS].body


@pytest.mark.parametrize("path", TRICKS)
def test_no_trick_ever_serves_a_file(app_and_cookie: tuple[FastAPI, str], path: str) -> None:
    app, cookie = app_and_cookie
    for sent_cookie in ("", cookie):
        status, body = _raw_get(app, path, sent_cookie)

        assert status != 200, (path, bool(sent_cookie))
        assert not _carries_a_file(body), (path, bool(sent_cookie))
        assert b"import " not in body and b"{%" not in body  # no source, no template


@pytest.mark.parametrize("method", ["HEAD", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"])
def test_only_get_is_public(app_and_cookie: tuple[FastAPI, str], method: str) -> None:
    app, _ = app_and_cookie
    status, body = _raw_get(app, P + CSS, method=method)

    assert status in (401, 403), method
    assert not _carries_a_file(body)


@pytest.mark.parametrize("name", [n for n in ASSETS.files if n.endswith(".svg")])
def test_the_served_svgs_hold_nothing_that_runs(
    app_and_cookie: tuple[FastAPI, str], name: str
) -> None:
    app, _ = app_and_cookie
    status, body = _raw_get(app, P + name)
    text = body.decode("utf-8").lower()

    assert status == 200
    assert "<script" not in text and "javascript:" not in text and "foreignobject" not in text
    assert not re.search(r"\son[a-z]+\s*=", text)
    assert not re.search(r"href\s*=\s*[\"'](?!#)", text)


# --- Conditional, partial and varied requests: always the whole file ------------------------

CONDITIONAL = [
    {"Range": "bytes=0-9"},
    {"Range": "bytes=-1"},
    {"If-None-Match": "*"},
    {"If-None-Match": '"x"'},
    {"If-Modified-Since": "Thu, 01 Jan 2099 00:00:00 GMT"},
    {"If-Range": '"x"', "Range": "bytes=0-1"},
    {"Accept-Encoding": "gzip, br, zstd"},
    {"Accept-Encoding": "identity;q=0"},
]


@pytest.mark.parametrize("headers", CONDITIONAL)
def test_conditional_and_partial_requests_get_the_whole_file(headers: dict[str, str]) -> None:
    for client in clients(HTTPS_URL):
        response = client.get(P + CSS, headers=headers)

        assert response.status_code == 200
        assert response.content == ASSETS.files[CSS].body
        assert "content-range" not in response.headers
        assert "content-encoding" not in response.headers
        assert response.headers["cache-control"] == IMMUTABLE


def _stable(response: httpx.Response) -> dict[str, str]:
    """Every header but X-Request-ID, which is new for each request on purpose."""
    return {k: v for k, v in response.headers.items() if k != "x-request-id"}


def test_a_query_string_does_not_change_a_static_answer() -> None:
    for client in clients(HTTPS_URL):
        plain = client.get(P + CSS)
        for query in ("?v=2", "?x=../../__init__.py", "?" + "a" * 2000, "?%00"):
            response = client.get(P + CSS + query)

            assert response.status_code == 200, query
            assert response.content == plain.content
            assert _stable(response) == _stable(plain)


def test_the_static_answer_does_not_depend_on_the_host() -> None:
    answers = []
    for base in (HTTPS_URL, "https://localhost", "http://localhost", "http://127.0.0.1:8000"):
        for client in clients(base):
            answers.append(client.get(P + CSS))
    first = answers[0]
    for response in answers:
        assert response.status_code == 200
        assert response.content == first.content
        assert _stable(response) == _stable(first)
