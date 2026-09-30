"""Invariants 12 and 13 (ANTIFAZ §6), over the routes the app really registers.

12: every registered route (with HEAD, OPTIONS, aliases and a trailing slash) needs the key,
    except an explicit allowlist; every proxy route runs the egress guard on the bytes it sends.
13: no key (Antifaz's or the provider's) ever appears in logs, error bodies or responses.
"""

import logging
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from fastapi.testclient import TestClient
from pydantic import SecretStr

from antifaz import Span, guard
from antifaz.api.app import create_app
from antifaz.api.gate import PUBLIC_PATHS
from antifaz.detect.scan import scan
from antifaz.errors import EgressBlocked
from antifaz.vault import Vault
from tests.integration.fakes import GATEWAY_KEY, FakeUpstream, misses_repeats, raiser
from tests.redteam.conftest import DNI, GATEWAY_BODY, both_echo, gateway_settings

AUTH = {"Authorization": f"Bearer {GATEWAY_KEY}"}
METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")


def _contexts(app: FastAPI) -> list[RouteContext]:
    # FastAPI keeps included routers in app.routes: their routes are listed through contexts.
    return list(iter_route_contexts(app.routes))


def _routes() -> list[str]:
    app = create_app(gateway_settings())
    return sorted({context.path for context in _contexts(app) if context.path})


def _aliases(path: str) -> list[str]:
    encoded_last = path[:-1] + f"%{ord(path[-1]):02X}"
    return [path, path + "/", "/" + path, path.upper(), encoded_last, path + "?x=1"]


def _proxy_routes(client: TestClient) -> list[tuple[str, set[str]]]:
    app: FastAPI = client.app  # type: ignore[assignment]
    routes = [
        (context.path, context.methods or set())
        for context in _contexts(app)
        if isinstance(context.original_route, APIRoute)
        and context.path
        and context.path not in PUBLIC_PATHS
    ]
    assert routes, "no proxy routes found: the test would pass without checking anything"
    return routes


# --- Invariant 12 ---------------------------------------------------------------------------


def test_the_allowlist_is_only_the_health_check() -> None:
    # Adding a public path must change this test on purpose.
    assert frozenset({"/healthz"}) == PUBLIC_PATHS


def test_registered_routes_are_the_expected_ones() -> None:
    # No /docs, /redoc or /openapi.json: they are disabled in the app.
    assert _routes() == [
        "/healthz",
        "/v1/chat/completions",
        "/v1/messages",
        "/v1/messages/count_tokens",
    ]


@pytest.mark.parametrize("path", _routes())
@pytest.mark.parametrize("method", METHODS)
def test_every_route_and_alias_needs_the_key(
    gateway: TestClient, upstream_any: FakeUpstream, path: str, method: str
) -> None:
    for alias in _aliases(path):
        response = gateway.request(method, alias)
        public = unquote(response.request.url.path) in PUBLIC_PATHS
        if public:
            assert response.status_code != 401
        else:
            assert response.status_code == 401, (method, alias)
            assert method == "HEAD" or response.json()["error"]["code"] == "unauthorized"
    assert upstream_any.requests == []


@pytest.mark.parametrize("method", METHODS)
def test_unknown_paths_need_the_key_too(gateway: TestClient, method: str) -> None:
    for path in ("/", "/docs", "/openapi.json", "/redoc", "/v2/anything", "/v1/models"):
        assert gateway.request(method, path).status_code == 401, (method, path)


def test_health_check_is_public_only_on_its_exact_path(gateway: TestClient) -> None:
    assert gateway.get("/healthz").status_code == 200
    assert gateway.head("/healthz").status_code == 405  # GET only, but no key asked
    for alias in ("/healthz/", "//healthz", "/HEALTHZ", "/healthz?x=1"):
        response = gateway.get(alias)
        if response.request.url.path == "/healthz":
            assert response.status_code == 200  # only the query string changed
        else:
            assert response.status_code == 401, alias


def test_every_proxy_route_runs_the_guard_on_the_sent_bytes(
    gateway: TestClient, upstream_any: FakeUpstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    checked: list[bytes] = []
    real_check = guard.check

    def spy(payload: bytes, vault: Vault) -> None:
        checked.append(payload)
        real_check(payload, vault)

    monkeypatch.setattr(guard, "check", spy)
    for path, methods in _proxy_routes(gateway):
        for method in methods:
            checked.clear()
            upstream_any.requests.clear()
            response = gateway.request(method, path, json=GATEWAY_BODY, headers=AUTH)

            assert response.status_code == 200, path
            assert len(checked) == 1, path
            assert [r.content for r in upstream_any.requests] == checked, path


def test_when_the_guard_blocks_no_proxy_route_sends_anything(
    gateway: TestClient, upstream_any: FakeUpstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    def block(payload: bytes, vault: Vault) -> None:
        raise EgressBlocked()

    monkeypatch.setattr(guard, "check", block)
    for path, methods in _proxy_routes(gateway):
        for method in methods:
            response = gateway.request(method, path, json=GATEWAY_BODY, headers=AUTH)

            assert response.status_code == 400, path
            assert response.json()["error"]["code"] == "antifaz_blocked"
    assert upstream_any.requests == []


# --- Invariant 13 ---------------------------------------------------------------------------

# Canary keys: invented, only for this test. If one shows up anywhere, a key leaked.
GATEWAY_CANARY = "canary-gateway-key-not-real-7f3a9c2e5b1d4a6f"
PROVIDER_CANARY = "canary-provider-key-not-real-4e8b2a6c0f1d3b5a"
CANARY_AUTH = {"Authorization": f"Bearer {GATEWAY_CANARY}"}
JSON_AUTH = {**CANARY_AUTH, "Content-Type": "application/json"}


def _echo_headers(status: int) -> Callable[[httpx.Request], httpx.Response]:
    """A careless provider that repeats the headers it received (the provider key) back."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"got": dict(request.headers)}})

    return handler


def _escaped_key(status: int) -> Callable[[httpx.Request], httpx.Response]:
    """A provider that repeats its key JSON-escaped: every character as a \\uXXXX escape."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers.get("x-api-key") or request.headers["authorization"][7:]
        escaped = "".join(f"\\u{ord(c):04x}" for c in key)
        body = '{"error": {"message": "bad key\\/' + escaped + '"}}'
        return httpx.Response(status, content=body.encode(), headers={"content-type": "x"})

    return handler


def _redirect(request: httpx.Request) -> httpx.Response:
    return httpx.Response(302, headers={"Location": f"https://x.invalid/?k={PROVIDER_CANARY}"})


# (name, upstream handler, method, path, request options)
Scenario = tuple[str, Callable[[httpx.Request], httpx.Response], str, str, dict[str, Any]]
BIG = "a" * 5000
SCENARIOS: list[Scenario] = [
    ("ok", both_echo, "POST", "", {"json": GATEWAY_BODY, "headers": CANARY_AUTH}),
    ("401 missing", both_echo, "POST", "", {"json": GATEWAY_BODY}),
    (
        "401 key plus a letter",
        both_echo,
        "POST",
        "",
        {"json": GATEWAY_BODY, "headers": {"x-api-key": GATEWAY_CANARY + "x"}},
    ),
    (
        "401 bearer lowercase",
        both_echo,
        "POST",
        "",
        {"json": GATEWAY_BODY, "headers": {"Authorization": f"bearer {GATEWAY_CANARY}"}},
    ),
    (
        "401 key in the path",
        both_echo,
        "GET",
        f"/v1/{GATEWAY_CANARY}?key={GATEWAY_CANARY}",
        {},
    ),
    (
        "404 key in the path with key",
        both_echo,
        "GET",
        f"/v1/{GATEWAY_CANARY}",
        {"headers": CANARY_AUTH},
    ),
    (
        "400 duplicate keys",
        both_echo,
        "POST",
        "",
        {"content": b'{"messages": [], "messages": []}', "headers": JSON_AUTH},
    ),
    ("400 invalid json", both_echo, "POST", "", {"content": b"{", "headers": JSON_AUTH}),
    (
        "400 guard",
        both_echo,
        "POST",
        "",
        {
            "json": {**GATEWAY_BODY, "messages": [{"role": "user", "content": f"{DNI} {DNI}"}]},
            "headers": CANARY_AUTH,
        },
    ),
    (
        "400 host",
        both_echo,
        "POST",
        "",
        {"json": GATEWAY_BODY, "headers": {**CANARY_AUTH, "Host": "evil.example"}},
    ),
    (
        "403 origin",
        both_echo,
        "POST",
        "",
        {"json": GATEWAY_BODY, "headers": {**CANARY_AUTH, "Origin": "https://evil.example"}},
    ),
    (
        "413",
        both_echo,
        "POST",
        "",
        {"json": {**GATEWAY_BODY, "x": BIG}, "headers": CANARY_AUTH},
    ),
    (
        "415",
        both_echo,
        "POST",
        "",
        {"content": b"{}", "headers": {**CANARY_AUTH, "Content-Type": "text/plain"}},
    ),
    (
        "502 connect",
        raiser(httpx.ConnectError),
        "POST",
        "",
        {"json": GATEWAY_BODY, "headers": CANARY_AUTH},
    ),
    (
        "504 timeout",
        raiser(httpx.ReadTimeout),
        "POST",
        "",
        {"json": GATEWAY_BODY, "headers": CANARY_AUTH},
    ),
    ("502 redirect", _redirect, "POST", "", {"json": GATEWAY_BODY, "headers": CANARY_AUTH}),
    (
        "provider error echoing its headers",
        _echo_headers(401),
        "POST",
        "",
        {"json": GATEWAY_BODY, "headers": CANARY_AUTH},
    ),
    (
        "provider error with the key escaped",
        _escaped_key(400),
        "POST",
        "",
        {"json": GATEWAY_BODY, "headers": CANARY_AUTH},
    ),
    (
        "provider success with the key escaped",
        _escaped_key(200),
        "POST",
        "",
        {"json": GATEWAY_BODY, "headers": CANARY_AUTH},
    ),
    (
        "provider success echoing its headers",
        _echo_headers(200),
        "POST",
        "",
        {"json": GATEWAY_BODY, "headers": CANARY_AUTH},
    ),
]


def _canary_app(
    upstream: FakeUpstream, detector: Callable[[str], Sequence[Span]] = scan
) -> TestClient:
    settings = gateway_settings(
        antifaz_api_key=SecretStr(GATEWAY_CANARY),
        openai_api_key=SecretStr(PROVIDER_CANARY),
        anthropic_api_key=SecretStr(PROVIDER_CANARY),
        max_body_bytes=4096,
        log_level="DEBUG",
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(settings, http_client=http, detector=detector)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s[0] for s in SCENARIOS])
@pytest.mark.parametrize(
    "route", ["/v1/chat/completions", "/v1/messages", "/v1/messages/count_tokens"]
)
def test_keys_never_leave_the_gateway(
    caplog: pytest.LogCaptureFixture, scenario: Scenario, route: str
) -> None:
    name, handler, method, path, options = scenario
    upstream = FakeUpstream(handler)
    client = _canary_app(upstream, detector=misses_repeats)
    # After create_app() (which configures logging): every logger at DEBUG, except httpx and
    # httpcore, which stay at WARNING as the app sets them (the test client logs its own URLs
    # through httpx at INFO).
    for logger in ("", "http", "uvicorn", "fastapi", "antifaz", "starlette"):
        logging.getLogger(logger or None).setLevel(logging.DEBUG)
    with client:
        response = client.request(method, path or route, **options)

    seen = [
        response.text,
        str(response.headers.raw),
        caplog.text,
        *(str(record.__dict__) for record in caplog.records),
    ]
    assert caplog.records, "nothing was logged: the check would be empty"
    for text in seen:
        assert GATEWAY_CANARY not in text, name
        assert PROVIDER_CANARY not in text, name
        unescaped = text.encode("ascii", "replace").decode("unicode_escape", "replace")
        assert PROVIDER_CANARY not in unescaped, name
    if name.startswith("provider"):
        assert response.status_code == 502, name
        assert response.json()["error"]["code"] == "bad_upstream_response"
    for request in upstream.requests:  # the gateway key never reaches the provider either
        assert GATEWAY_CANARY not in str(request.headers)
        assert GATEWAY_CANARY.encode() not in request.content
