"""The container healthcheck (`python -m antifaz.healthcheck`): no curl in the image."""

import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from antifaz import healthcheck


def test_host_header_is_the_first_allowed_host() -> None:
    assert healthcheck.host_header(["antifaz.internal", "localhost"]) == "antifaz.internal"


def test_a_wildcard_host_becomes_a_concrete_subdomain() -> None:
    assert healthcheck.host_header(["*.example.com"]) == "healthcheck.example.com"


def test_no_allowed_hosts_falls_back_to_loopback() -> None:
    assert healthcheck.host_header([]) == "127.0.0.1"


class _Response(io.BytesIO):
    def __init__(self, status: int, body: bytes) -> None:
        super().__init__(body)
        self.status = status

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class _Opener:
    def __init__(self, open_: Any) -> None:
        self.open = open_


def _fake_urlopen(status: int, body: bytes, seen: list[urllib.request.Request]) -> Any:
    def urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        seen.append(request)
        return _Response(status, body)

    return lambda: _Opener(urlopen)


def test_the_check_never_goes_through_an_http_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://proxy.invalid:3128")

    def proxied(opener: urllib.request.OpenerDirector) -> bool:
        # `handlers` and `proxies` are real attributes that typeshed does not declare.
        return any(
            isinstance(handler, urllib.request.ProxyHandler) and bool(handler.proxies)  # type: ignore[attr-defined]
            for handler in opener.handlers  # type: ignore[attr-defined]
        )

    # A default opener would send the check to the proxy; ours never does.
    assert proxied(urllib.request.build_opener())
    assert not proxied(healthcheck.make_opener())


def test_healthy_gateway_exits_0_and_sends_an_allowed_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTIFAZ_ALLOWED_HOSTS", "antifaz,localhost")
    seen: list[urllib.request.Request] = []
    body = json.dumps({"status": "ok", "version": "0.1.0"}).encode()
    monkeypatch.setattr(healthcheck, "make_opener", _fake_urlopen(200, body, seen))

    assert healthcheck.main() == 0
    assert seen[0].full_url == "http://127.0.0.1:8000/healthz"
    assert seen[0].get_header("Host") == "antifaz"


def test_a_body_that_is_not_ok_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []
    body = json.dumps({"status": "starting"}).encode()
    monkeypatch.setattr(healthcheck, "make_opener", _fake_urlopen(200, body, seen))

    assert healthcheck.main() == 1


def test_a_non_json_body_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []
    monkeypatch.setattr(healthcheck, "make_opener", _fake_urlopen(200, b"<html>", seen))

    assert healthcheck.main() == 1


def test_an_unreachable_gateway_exits_1_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(healthcheck, "make_opener", lambda: _Opener(urlopen))

    assert healthcheck.main() == 1
    assert "Traceback" not in capsys.readouterr().err
