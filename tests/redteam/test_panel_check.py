"""Attacks on «Comprobar ahora» (issue 53, PR 11e-4, ADR-0018 and invariant 13).

The check calls the real providers' model lists, so it is a new place where a provider can
answer with something of the key. Each attack uses invented canary keys and a fake provider
(httpx.MockTransport); the socket guard refuses any connect that is not loopback, so a real
provider is never called.

- A provider that answers with the key, a provider-style masked fragment (`sk-proj-****…WxYz`)
  and a 10-character run of the key, in the body, in the headers and in a Location: none of it
  reaches the answer's bytes or the logs (checked with KeyFragments, the gateway's own matcher,
  and with an independent oracle).
- A redirect is never followed: its target is called zero times.
- A base URL with a user or password is never called.
- The check uses the gateway's own client: building any new httpx client during a check fails
  the check (it would be a new way out), the client that sends is app.state.http_client, and the
  host and path are the ones /v1/models uses. With HTTP(S)_PROXY set, it still builds no client
  and sends through that same one (review 7 of 11e-4: reworded, it proves no new way out, not
  which proxy the gateway's client honours).
- httpx and httpcore log nothing about the check, with a REAL httpx client over loopback (so
  httpcore runs) and the gateway at DEBUG; a companion test shows the same press does log both
  when their loggers are not kept quiet, so the check is not empty (review 7).
"""

import contextlib
import http.server
import logging
import threading
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import antifaz.logging
from antifaz.api.app import create_app
from antifaz.api.key_fragments import KeyFragments
from tests.conftest import forbid_network
from tests.integration.fakes import FakeUpstream
from tests.integration.panel_client import (
    ADMIN_TOKEN,
    HTTPS_URL,
    action_headers,
    logged_in,
    panel_client,
    panel_settings,
)

# Invented canaries (never real keys).
OPENAI = "sk-proj-QzChkCanaryOpnWvXkMtRpLsYjGhNqZuVoPiYkGxRw"
ANTHROPIC = "sk-ant-api03-QzChkCanaryAntWvXkMtRpLsYjGhNqZuVoPj-Qw"
GATEWAY = "QzChkGateCanaryVxJmPtRnLsYhGfDcBaWqUoIeKy"
PATH = "/panel/api/status/check"


def _leaks(key: str) -> list[str]:
    """What a provider could echo of `key`: the key, a masked tail and head, a 10-char run."""
    secret = key.removeprefix("sk-proj-").removeprefix("sk-ant-api03-")
    prefix = key[: len(key) - len(secret)]
    return [key, f"{prefix}****...{secret[-4:]}", f"{prefix}{secret[:4]}****", secret[7:17]]


LEAKS = [*_leaks(OPENAI), *_leaks(ANTHROPIC)]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_network(monkeypatch)


def _settings(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "antifaz_api_key": SecretStr(GATEWAY),
        "openai_api_key": SecretStr(OPENAI),
        "anthropic_api_key": SecretStr(ANTHROPIC),
        "openai_base_url": "https://openai.check.invalid/v1",
        "anthropic_base_url": "https://anthropic.check.invalid",
    }
    values.update(overrides)
    return panel_settings(**values)


def _client(
    handler: Callable[[httpx.Request], httpx.Response], **overrides: Any
) -> tuple[Iterator[TestClient], FakeUpstream]:
    upstream = FakeUpstream(handler)

    def make() -> Iterator[TestClient]:
        with panel_client(HTTPS_URL, settings=_settings(**overrides), upstream=upstream) as client:
            yield client

    return make(), upstream


def _press(client: TestClient) -> httpx.Response:
    session_id, token = logged_in(client)
    return client.post(PATH, content=b"{}", headers=action_headers(client, session_id, token))


def _echo_everything(status: int) -> Callable[[httpx.Request], httpx.Response]:
    def answer(request: httpx.Request) -> httpx.Response:
        joined = " | ".join(LEAKS)
        headers = {
            "X-Echo": joined,
            "WWW-Authenticate": f'Bearer error="{OPENAI}"',
            "Location": f"https://evil.check.invalid/{ANTHROPIC}?k={OPENAI}",
        }
        return httpx.Response(status, headers=headers, text=f"Incorrect API key: {joined}")

    return answer


def _nothing_of_a_key(text: str) -> None:
    matcher = KeyFragments([OPENAI, ANTHROPIC, GATEWAY, ADMIN_TOKEN])
    assert not matcher.found_in(text)
    for leak in LEAKS:
        assert leak not in text


@pytest.mark.parametrize("status", [200, 401, 403, 302, 500])
def test_a_provider_echoing_the_key_everywhere_leaks_nothing(
    status: int, caplog: pytest.LogCaptureFixture
) -> None:
    """The fake provider puts the key and its fragments in the body, headers and Location."""
    caplog.set_level(logging.DEBUG)
    clients, upstream = _client(_echo_everything(status))
    for client in clients:
        response = _press(client)

        assert response.status_code == 200
        _nothing_of_a_key(response.content.decode("utf-8"))
        _nothing_of_a_key(str(dict(response.headers)))
    _nothing_of_a_key(caplog.text)
    assert len(upstream.requests) == 2


def test_a_redirect_target_is_called_zero_times() -> None:
    """A 302 to another host is reported as a redirect and never followed."""
    clients, upstream = _client(_echo_everything(302))
    for client in clients:
        body = _press(client).json()

    assert [p["result"] for p in body["providers"]] == ["redirect", "redirect"]
    assert {r.url.host for r in upstream.requests} == {
        "openai.check.invalid",
        "anthropic.check.invalid",
    }
    assert not any("evil" in str(r.url) for r in upstream.requests)


def test_a_base_url_with_a_password_is_never_called() -> None:
    """https://user:<key>@host: the panel does not send it (nor the key in the userinfo)."""
    base = f"https://admin:{OPENAI}@openai.check.invalid/v1"
    clients, upstream = _client(_echo_everything(200), openai_base_url=base)
    for client in clients:
        body = _press(client).json()

    assert body["providers"][0] == {
        "id": "openai",
        "result": "not_checked_credentials",
        "http_status": None,
    }
    assert [r.url.host for r in upstream.requests] == ["anthropic.check.invalid"]


def test_building_a_new_http_client_during_a_check_fails_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No new egress: any httpx client made while the check runs breaks the test."""
    clients, upstream = _client(_echo_everything(200))
    for client in clients:
        session_id, token = logged_in(client)

        def refuse(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("the check built a new HTTP client")

        monkeypatch.setattr(httpx.AsyncClient, "__init__", refuse)
        monkeypatch.setattr(httpx.Client, "__init__", refuse)
        response = client.post(
            PATH, content=b"{}", headers=action_headers(client, session_id, token)
        )
        monkeypatch.undo()
        forbid_network(monkeypatch)

        assert response.status_code == 200
        assert [p["result"] for p in response.json()["providers"]] == ["ok", "ok"]
    assert len(upstream.requests) == 2


def test_the_client_that_sends_is_the_gateways_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same object as app.state.http_client, and the same host and path as GET /v1/models."""
    senders: list[object] = []
    real_stream = httpx.AsyncClient.stream

    def spy(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> Any:
        senders.append(self)
        assert kwargs.get("follow_redirects") is False
        return real_stream(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "stream", spy)
    clients, upstream = _client(_echo_everything(200))
    for client in clients:
        own = client.app.state.http_client  # type: ignore[attr-defined]
        _press(client)
        checked = [(r.url.host, r.url.path) for r in upstream.requests]
        upstream.requests.clear()
        auth = {"Authorization": f"Bearer {GATEWAY}"}
        client.get("/v1/models", headers=auth)
        client.get("/v1/models", headers={"x-api-key": GATEWAY, "anthropic-version": "2023-06-01"})
        listed = [(r.url.host, r.url.path) for r in upstream.requests]

    assert senders and all(sender is own for sender in senders)
    assert len(senders) == 2
    assert sorted(checked) == sorted(listed)


def test_with_a_proxy_in_the_environment_the_check_builds_no_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP(S)_PROXY set: the check builds no httpx client (nothing that could read those
    variables for it) and sends through app.state.http_client. Honest scope: with an injected
    client this proves the check takes no new way out; which proxy the gateway's own client
    honours is that client's business (providers/http.py), the same as for the proxy routes."""
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    senders: list[object] = []
    real_stream = httpx.AsyncClient.stream

    def spy(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> Any:
        senders.append(self)
        return real_stream(self, *args, **kwargs)

    built: list[str] = []

    def refuse(*args: Any, **kwargs: Any) -> None:
        built.append("client")
        raise AssertionError("the check built a new HTTP client")

    clients, upstream = _client(_echo_everything(200))
    for client in clients:
        session_id, token = logged_in(client)
        own = client.app.state.http_client  # type: ignore[attr-defined]
        with monkeypatch.context() as during:
            during.setattr(httpx.AsyncClient, "stream", spy)
            during.setattr(httpx.AsyncClient, "__init__", refuse)
            during.setattr(httpx.Client, "__init__", refuse)
            response = client.post(
                PATH, content=b"{}", headers=action_headers(client, session_id, token)
            )

    assert response.status_code == 200
    assert built == []
    assert len(senders) == 2 and all(sender is own for sender in senders)
    assert len(upstream.requests) == 2


class _RefusingProvider(http.server.BaseHTTPRequestHandler):
    """A loopback stand-in for a provider: every GET is a 401 (never a real provider)."""

    def do_GET(self) -> None:  # the name http.server calls
        self.send_response(401)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


@contextlib.contextmanager
def _real_http_gateway() -> Iterator[TestClient]:
    """The panel with a REAL httpx client (so httpcore really runs) to a loopback server, the
    gateway's log level at DEBUG."""
    provider = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RefusingProvider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{provider.server_address[1]}"
    try:
        settings = _settings(openai_base_url=base, anthropic_base_url=base, log_level="DEBUG")
        http_client = httpx.AsyncClient(trust_env=False)
        app = create_app(settings, http_client=http_client)
        with TestClient(app, base_url=HTTPS_URL, raise_server_exceptions=False) as client:
            yield client
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(10)


def _http_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name.split(".")[0] in ("httpx", "httpcore")]


def test_httpx_and_httpcore_log_nothing_about_the_check(caplog: pytest.LogCaptureFixture) -> None:
    """A real httpx client over loopback (httpcore runs) and the gateway at DEBUG: httpx and
    httpcore log nothing about the check (their loggers stay at WARNING)."""
    caplog.set_level(logging.DEBUG)
    with _real_http_gateway() as client:
        caplog.clear()
        body = _press(client).json()

    assert [p["result"] for p in body["providers"]] == ["refused", "refused"]
    assert _http_records(caplog) == []
    assert "127.0.0.1:" not in " ".join(r.getMessage() for r in caplog.records)


def test_the_log_check_would_see_httpx_and_httpcore_if_they_were_not_quiet(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The guard of the test above is not empty: without the quiet loggers, the same press
    gives httpx and httpcore records."""
    monkeypatch.setattr(antifaz.logging, "QUIET_LOGGERS", ())
    loggers = [logging.getLogger(name) for name in ("httpx", "httpcore")]
    levels = [logger.level for logger in loggers]
    caplog.set_level(logging.DEBUG)
    try:
        for logger in loggers:
            logger.setLevel(logging.NOTSET)
        with _real_http_gateway() as client:
            caplog.clear()
            _press(client)
    finally:
        for logger, level in zip(loggers, levels, strict=True):
            logger.setLevel(level)

    names = {r.name.split(".")[0] for r in _http_records(caplog)}
    assert names == {"httpx", "httpcore"}
