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


# Every route that needs the key, by what it sends to a provider. A new route must be put in
# one of these on purpose (test_every_route_is_classified), so none escapes the checks below.
# Classified by (path, method): a new method on an existing path must be classified too.
BODY_PROXY_ROUTES = frozenset(
    {
        ("/v1/chat/completions", "POST"),
        ("/v1/messages", "POST"),
        ("/v1/messages/count_tokens", "POST"),
    }
)
# Sends no body: only allowlisted query parameters, which pass through the detector.
QUERY_PROXY_ROUTES = frozenset({("/v1/models", "GET")})
# Never calls a provider.
LOCAL_ROUTES = frozenset({("/antifaz/scan", "POST")})


def _keyed_routes(client: TestClient) -> list[tuple[str, set[str]]]:
    app: FastAPI = client.app  # type: ignore[assignment]
    routes = [
        (context.path, context.methods or set())
        for context in _contexts(app)
        if isinstance(context.original_route, APIRoute)
        and context.path
        and context.path not in PUBLIC_PATHS
    ]
    assert routes, "no routes found: the test would pass without checking anything"
    return routes


def _keyed_pairs(client: TestClient) -> set[tuple[str, str]]:
    return {(path, method) for path, methods in _keyed_routes(client) for method in methods}


def _proxy_routes(client: TestClient) -> list[tuple[str, str]]:
    return sorted(_keyed_pairs(client) & BODY_PROXY_ROUTES)


# --- Invariant 12 ---------------------------------------------------------------------------


def test_the_allowlist_is_only_the_health_check() -> None:
    # Adding a public path must change this test on purpose.
    assert frozenset({"/healthz"}) == PUBLIC_PATHS


def test_registered_routes_are_the_expected_ones() -> None:
    # No /docs, /redoc or /openapi.json: they are disabled in the app.
    assert _routes() == [
        "/antifaz/scan",
        "/healthz",
        "/v1/chat/completions",
        "/v1/messages",
        "/v1/messages/count_tokens",
        "/v1/models",
    ]


def test_every_route_is_classified(gateway: TestClient) -> None:
    keyed = _keyed_pairs(gateway)
    classes = (BODY_PROXY_ROUTES, QUERY_PROXY_ROUTES, LOCAL_ROUTES)

    assert set().union(*classes) == keyed  # every (path, method) is classified, none extra
    assert sum(len(c) for c in classes) == len(keyed)  # each pair in exactly one class
    # The query and local routes take exactly these methods, nothing else.
    assert {pair for pair in keyed if pair[0] == "/v1/models"} == {("/v1/models", "GET")}
    assert {pair for pair in keyed if pair[0] == "/antifaz/scan"} == {("/antifaz/scan", "POST")}


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
    for path in ("/", "/docs", "/openapi.json", "/redoc", "/v2/anything", "/v1/model"):
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
    for path, method in _proxy_routes(gateway):
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
    for path, method in _proxy_routes(gateway):
        response = gateway.request(method, path, json=GATEWAY_BODY, headers=AUTH)

        assert response.status_code == 400, path
        assert response.json()["error"]["code"] == "antifaz_blocked"
    assert upstream_any.requests == []


def test_the_query_route_sends_no_body_and_blocks_personal_data_in_its_parameters(
    gateway: TestClient, upstream_any: FakeUpstream
) -> None:
    headers = {**AUTH, "anthropic-version": "2023-06-01"}
    for path, method in sorted(QUERY_PROXY_ROUTES):
        upstream_any.requests.clear()
        ok = gateway.request(method, path, params={"after_id": "claude-x"}, headers=headers)
        assert ok.is_success, path
        assert [r.content for r in upstream_any.requests] == [b""], path

        upstream_any.requests.clear()
        response = gateway.request(method, path, params={"after_id": DNI}, headers=headers)

        assert response.status_code == 400, path
        assert response.json()["error"]["code"] == "antifaz_blocked"
        assert upstream_any.requests == [], path


def test_local_routes_never_call_a_provider(
    gateway: TestClient, upstream_any: FakeUpstream
) -> None:
    for path, method in sorted(LOCAL_ROUTES):
        response = gateway.request(method, path, json={"text": f"DNI {DNI}"}, headers=AUTH)

        assert response.status_code == 200, path
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
        "400 key in anthropic-version",
        both_echo,
        "POST",
        "",
        {"json": GATEWAY_BODY, "headers": {**CANARY_AUTH, "anthropic-version": GATEWAY_CANARY}},
    ),
    (
        "400 key in anthropic-beta",
        both_echo,
        "POST",
        "",
        {
            "json": GATEWAY_BODY,
            "headers": {
                **CANARY_AUTH,
                "anthropic-version": "2023-06-01",
                "anthropic-beta": GATEWAY_CANARY,
            },
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
    _check_no_key_leaves(caplog, name, handler, method, path or route, options)


def _check_no_key_leaves(
    caplog: pytest.LogCaptureFixture,
    name: str,
    handler: Callable[[httpx.Request], httpx.Response],
    method: str,
    path: str,
    options: dict[str, Any],
    detector: Callable[[str], Sequence[Span]] = misses_repeats,
) -> None:
    upstream = FakeUpstream(handler)
    client = _canary_app(upstream, detector=detector)
    # After create_app() (which configures logging): every logger at DEBUG, except httpx and
    # httpcore, which stay at WARNING as the app sets them (the test client logs its own URLs
    # through httpx at INFO).
    for logger in ("", "http", "uvicorn", "fastapi", "antifaz", "starlette"):
        logging.getLogger(logger or None).setLevel(logging.DEBUG)
    with client:
        response = client.request(method, path, **options)

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
        assert GATEWAY_CANARY not in unquote(str(request.url))
        assert GATEWAY_CANARY.encode() not in request.content


# --- Invariant 13 on GET /v1/models and POST /antifaz/scan (issue 29) ----------------------

ANTHROPIC_CANARY = {"x-api-key": GATEWAY_CANARY, "anthropic-version": "2023-06-01"}
GET_SCENARIOS: list[tuple[str, Callable[[httpx.Request], httpx.Response], dict[str, Any]]] = [
    ("ok", both_echo, {}),
    ("401 missing", both_echo, {"headers": {}}),
    ("401 key plus a letter", both_echo, {"headers": {"x-api-key": GATEWAY_CANARY + "x"}}),
    ("400 unknown parameter holding the key", both_echo, {"params": {"key": GATEWAY_CANARY}}),
    ("400 the key as a cursor", both_echo, {"params": {"after_id": GATEWAY_CANARY}}),
    ("400 the provider key as a cursor", both_echo, {"params": {"before_id": PROVIDER_CANARY}}),
    (
        "400 header",
        both_echo,
        {"headers": {**ANTHROPIC_CANARY, "anthropic-version": f"x {GATEWAY_CANARY}"}},
    ),
    (
        "403 origin",
        both_echo,
        {"headers": {**ANTHROPIC_CANARY, "Origin": "https://evil.example"}},
    ),
    (
        "400 key in anthropic-version",
        both_echo,
        {"headers": {**CANARY_AUTH, "anthropic-version": GATEWAY_CANARY}},
    ),
    (
        "400 key in anthropic-beta",
        both_echo,
        {"headers": {**ANTHROPIC_CANARY, "anthropic-beta": GATEWAY_CANARY}},
    ),
    (
        "400 key split across two cursors",
        both_echo,
        {
            "params": {"after_id": GATEWAY_CANARY[:20], "before_id": GATEWAY_CANARY[20:]},
            "headers": ANTHROPIC_CANARY,
        },
    ),
    ("502 connect", raiser(httpx.ConnectError), {}),
    ("504 timeout", raiser(httpx.ReadTimeout), {}),
    ("502 redirect", _redirect, {}),
    ("provider error echoing its headers", _echo_headers(401), {}),
    ("provider error with the key escaped", _escaped_key(400), {}),
    ("provider success with the key escaped", _escaped_key(200), {}),
    ("provider success echoing its headers", _echo_headers(200), {}),
]


@pytest.mark.parametrize("scenario", GET_SCENARIOS, ids=[s[0] for s in GET_SCENARIOS])
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_keys_never_leave_the_model_list(
    caplog: pytest.LogCaptureFixture,
    scenario: tuple[str, Callable[[httpx.Request], httpx.Response], dict[str, Any]],
    provider: str,
) -> None:
    name, handler, options = scenario
    auth = ANTHROPIC_CANARY if provider == "anthropic" else CANARY_AUTH
    options = {**options, "headers": options.get("headers", auth)}
    if provider == "openai" and name.startswith("400 header"):
        options["headers"] = {**CANARY_AUTH, "anthropic-version": f"x {GATEWAY_CANARY}"}
    _check_no_key_leaves(caplog, name, handler, "GET", "/v1/models", options)


def _broken_detector(text: str) -> Sequence[Span]:
    raise RuntimeError(f"detector failed on {text} {GATEWAY_CANARY}")


SCAN_SCENARIOS: list[tuple[str, dict[str, Any]]] = [
    ("ok", {"json": {"text": f"{DNI} {GATEWAY_CANARY}"}, "headers": CANARY_AUTH}),
    ("401 missing", {"json": {"text": "hola"}}),
    ("400 invalid json", {"content": b"{", "headers": JSON_AUTH}),
    (
        "400 duplicate keys",
        {"content": b'{"text": "a", "text": "b"}', "headers": JSON_AUTH},
    ),
    ("400 other field", {"json": {"text": "a", GATEWAY_CANARY: 1}, "headers": CANARY_AUTH}),
    ("413", {"json": {"text": BIG}, "headers": CANARY_AUTH}),
    ("415", {"content": b"{}", "headers": {**CANARY_AUTH, "Content-Type": "text/plain"}}),
    ("403 origin", {"json": {"text": "a"}, "headers": {**CANARY_AUTH, "Origin": "https://x.y"}}),
]


@pytest.mark.parametrize("scenario", SCAN_SCENARIOS, ids=[s[0] for s in SCAN_SCENARIOS])
def test_keys_never_leave_the_scan_route(
    caplog: pytest.LogCaptureFixture, scenario: tuple[str, dict[str, Any]]
) -> None:
    name, options = scenario
    _check_no_key_leaves(caplog, name, both_echo, "POST", "/antifaz/scan", options, scan)


def test_keys_never_leave_the_scan_route_when_the_detector_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    options = {"json": {"text": f"hola {GATEWAY_CANARY}"}, "headers": CANARY_AUTH}
    _check_no_key_leaves(
        caplog, "detector", both_echo, "POST", "/antifaz/scan", options, _broken_detector
    )
