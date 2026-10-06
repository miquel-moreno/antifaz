"""`antifaz doctor` (issue 43, ADR-0017): configuration, gateway and (opt-in) provider checks.

Every test works in pytest's tmp_path with the ANTIFAZ_* environment cleared: the real .env of
the repository is never read. Keys are canaries made at runtime. The gateway and the providers
are httpx.MockTransport fakes, or tiny servers on 127.0.0.1; any other connection fails.
"""

import gzip
import io
import json
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from antifaz.cli import doctor, main
from tests.conftest import forbid_network

PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


def canary(prefix: str) -> str:
    """A fake key, different on every run, never a literal in the source."""
    return f"{prefix}canary{secrets.token_hex(20)}"


@dataclass
class Keys:
    gateway: str = field(default_factory=lambda: secrets.token_hex(32))
    openai: str = field(default_factory=lambda: canary("sk-"))
    anthropic: str = field(default_factory=lambda: canary("sk-" + "ant-"))

    def all(self) -> list[str]:
        return [self.gateway, self.openai, self.anthropic]


@pytest.fixture
def canaries() -> Keys:
    return Keys()


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No ANTIFAZ_* or proxy variable from the shell; the current folder is tmp_path."""
    for name in list(os.environ):
        if name.upper().startswith("ANTIFAZ_") or name.upper() in PROXY_VARIABLES:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    forbid_network(monkeypatch)


def write_env(folder: Path, canaries: Keys, **extra: str) -> None:
    lines = {
        "ANTIFAZ_API_KEY": canaries.gateway,
        "ANTIFAZ_OPENAI_API_KEY": canaries.openai,
        "ANTIFAZ_ANTHROPIC_API_KEY": canaries.anthropic,
        "ANTIFAZ_ALLOWED_HOSTS": "localhost,127.0.0.1",
    }
    lines |= extra
    text = "".join(f"{name}={value}\n" for name, value in lines.items() if value)
    (folder / ".env").write_text(text, encoding="utf-8", newline="\n")


Handler = Callable[[httpx.Request], httpx.Response]


@dataclass
class Fake:
    """A MockTransport handler that records every request."""

    handler: Handler
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def healthy(ner: str | None = None) -> Handler:
    body: dict[str, Any] = {"status": "ok", "version": "0.2.0"}
    if ner is not None:
        body["ner"] = ner
    return lambda request: httpx.Response(200, json=body)


def models_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"object": "list", "data": [{"id": "fake-model"}]})


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], Fake]:
    def install(handler: Handler) -> Fake:
        fake = Fake(handler)
        monkeypatch.setattr(doctor, "_gateway_transport", lambda: httpx.MockTransport(fake))
        return fake

    return install


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], Fake]:
    def install(handler: Handler) -> Fake:
        fake = Fake(handler)
        monkeypatch.setattr(doctor, "_provider_transport", lambda: httpx.MockTransport(fake))
        return fake

    return install


@dataclass
class Result:
    code: int
    out: str
    err: str
    logs: str

    def holds_any(self, secrets_: list[str]) -> bool:
        everything = self.out + self.err + self.logs
        return any(secret in everything for secret in secrets_)


@pytest.fixture
def run(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> Callable[..., Result]:
    def _run(*args: str) -> Result:
        caplog.set_level(logging.DEBUG)
        code = main(["doctor", *args])
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err, caplog.text)

    return _run


# --- Everything fine ---------------------------------------------------------------------------


def test_a_good_configuration_and_a_healthy_gateway_pass(
    tmp_path: Path, canaries: Keys, gateway: Any, providers: Any, run: Any
) -> None:
    write_env(tmp_path, canaries)
    health = gateway(healthy())
    provider = providers(models_ok)
    result = run()
    assert result.code == 0
    assert "configuration: the gateway would start" in result.out
    assert "providers with a key: OpenAI, Anthropic" in result.out
    assert "NER (names and addresses): off" in result.out
    assert "gateway: up, version 0.2.0" in result.out
    assert "all checks passed" in result.out
    assert len(health.requests) == 1
    assert provider.requests == []  # no --providers: no provider is called at all
    assert not result.holds_any(canaries.all())


def test_next_steps_are_printed(tmp_path: Path, canaries: Keys, gateway: Any, run: Any) -> None:
    write_env(tmp_path, canaries)
    gateway(healthy())
    out = run().out
    assert "antifaz verify" in out
    assert "antifaz setup claude-code" in out
    assert doctor.DOCS_URL in out


def test_healthz_gets_no_key_and_an_allowed_host(
    tmp_path: Path, canaries: Keys, gateway: Any, run: Any
) -> None:
    write_env(tmp_path, canaries, ANTIFAZ_ALLOWED_HOSTS="antifaz.internal,localhost")
    health = gateway(healthy())
    assert run("--url", "http://127.0.0.1:8000/").code == 0
    request = health.requests[0]
    assert request.method == "GET"
    assert str(request.url) == "http://127.0.0.1:8000/healthz"
    assert request.headers["host"] == "antifaz.internal"
    assert "authorization" not in request.headers
    assert "x-api-key" not in request.headers
    assert canaries.gateway not in json.dumps(dict(request.headers))


def test_providers_get_their_model_list_with_their_own_key(
    tmp_path: Path, canaries: Keys, gateway: Any, providers: Any, run: Any
) -> None:
    write_env(
        tmp_path,
        canaries,
        ANTIFAZ_OPENAI_BASE_URL="https://openai.fake.invalid/v1/",
        ANTIFAZ_ANTHROPIC_BASE_URL="https://anthropic.fake.invalid",
    )
    gateway(healthy())
    provider = providers(models_ok)
    result = run("--providers")
    assert result.code == 0, result.out
    assert "OpenAI: the key works" in result.out
    assert "Anthropic: the key works" in result.out
    assert "free" in result.out
    openai, anthropic = provider.requests
    assert (openai.method, str(openai.url)) == ("GET", "https://openai.fake.invalid/v1/models")
    assert openai.headers["authorization"] == f"Bearer {canaries.openai}"
    assert "x-api-key" not in openai.headers
    assert str(anthropic.url) == "https://anthropic.fake.invalid/v1/models"
    assert anthropic.headers["x-api-key"] == canaries.anthropic
    assert anthropic.headers["anthropic-version"] == doctor.ANTHROPIC_VERSION
    assert "authorization" not in anthropic.headers
    for request in provider.requests:
        sent = json.dumps(dict(request.headers)) + str(request.url) + request.content.decode()
        assert canaries.gateway not in sent
    assert not result.holds_any(canaries.all())


def test_only_providers_with_a_key_are_checked(
    tmp_path: Path, canaries: Keys, gateway: Any, providers: Any, run: Any
) -> None:
    write_env(tmp_path, canaries, ANTIFAZ_ANTHROPIC_API_KEY="")
    gateway(healthy())
    provider = providers(models_ok)
    result = run("--providers")
    assert result.code == 0
    assert "providers with a key: OpenAI" in result.out
    assert [str(r.url) for r in provider.requests] == ["https://api.openai.com/v1/models"]


def test_no_provider_key_is_a_note_not_a_failure(
    tmp_path: Path, canaries: Keys, gateway: Any, providers: Any, run: Any
) -> None:
    write_env(tmp_path, canaries, ANTIFAZ_OPENAI_API_KEY="", ANTIFAZ_ANTHROPIC_API_KEY="")
    gateway(healthy())
    provider = providers(models_ok)
    result = run("--providers")
    assert result.code == 0
    assert "none (every proxy route answers 503)" in result.out
    assert "none has a key, nothing to check" in result.out
    assert provider.requests == []


# --- Provider errors: generic messages, never a key or a body ----------------------------------


def _echo_key(status: int) -> Handler:
    """A careless provider: its error body repeats the key it got, and so does a header."""

    def handler(request: httpx.Request) -> httpx.Response:
        got = request.headers.get("authorization") or request.headers.get("x-api-key") or ""
        body = {"error": {"message": f"Incorrect API key provided: {got}"}}
        return httpx.Response(status, json=body, headers={"x-echo": got})

    return handler


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_fails_without_printing_it(
    tmp_path: Path, canaries: Keys, gateway: Any, providers: Any, run: Any, status: int
) -> None:
    write_env(tmp_path, canaries)
    gateway(healthy())
    providers(_echo_key(status))
    result = run("--providers")
    assert result.code == 1
    refused = f"OpenAI: the provider refused the key (HTTP {status})"
    assert f"{refused}: check ANTIFAZ_OPENAI_API_KEY" in result.out
    assert "check ANTIFAZ_ANTHROPIC_API_KEY" in result.out
    assert "Incorrect API key" not in result.out + result.err + result.logs
    assert not result.holds_any(canaries.all())


def test_a_redirect_is_not_followed(
    tmp_path: Path, canaries: Keys, gateway: Any, providers: Any, run: Any
) -> None:
    write_env(tmp_path, canaries)
    gateway(healthy())

    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(307, headers={"location": "https://elsewhere.fake.invalid/models"})

    provider = providers(redirect)
    result = run("--providers")
    assert result.code == 1
    assert "redirect (HTTP 307), not followed: check ANTIFAZ_OPENAI_BASE_URL" in result.out
    assert all("elsewhere" not in str(r.url) for r in provider.requests)
    assert len(provider.requests) == 2  # one per provider, never a second hop
    assert "elsewhere" not in result.out
    assert not result.holds_any(canaries.all())


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (httpx.ReadTimeout, "OpenAI: no answer within 5 s (check ANTIFAZ_OPENAI_BASE_URL)"),
        (httpx.ConnectTimeout, "OpenAI: no answer within 5 s"),
        (httpx.ConnectError, "OpenAI: cannot reach the provider (check ANTIFAZ_OPENAI_BASE_URL"),
        (httpx.UnsupportedProtocol, "OpenAI: ANTIFAZ_OPENAI_BASE_URL is not a valid http(s) URL"),
        (RuntimeError, "OpenAI: cannot reach the provider"),
    ],
)
def test_network_errors_are_generic(
    tmp_path: Path,
    canaries: Keys,
    gateway: Any,
    providers: Any,
    run: Any,
    error: type[Exception],
    message: str,
) -> None:
    write_env(tmp_path, canaries)
    gateway(healthy())

    def fail(request: httpx.Request) -> httpx.Response:
        # The exception message repeats the key, as a careless library could.
        raise error(f"failed with {request.headers.get('authorization')}")

    providers(fail)
    result = run("--providers")
    assert result.code == 1
    assert message in result.out
    assert "failed with" not in result.out + result.err + result.logs
    assert not result.holds_any(canaries.all())


def test_an_unexpected_status_is_reported_with_its_code_only(
    tmp_path: Path, canaries: Keys, gateway: Any, providers: Any, run: Any
) -> None:
    write_env(tmp_path, canaries)
    gateway(healthy())
    providers(_echo_key(500))
    result = run("--providers")
    assert result.code == 1
    assert "OpenAI: unexpected answer (HTTP 500)" in result.out
    assert not result.holds_any(canaries.all())


def test_a_base_url_with_a_password_is_named_and_not_called(
    tmp_path: Path, canaries: Keys, gateway: Any, providers: Any, run: Any
) -> None:
    password = canary("pw")
    write_env(
        tmp_path, canaries, ANTIFAZ_OPENAI_BASE_URL=f"https://user:{password}@fake.invalid/v1"
    )
    gateway(healthy())
    provider = providers(models_ok)
    result = run("--providers")
    assert result.code == 1
    assert "ANTIFAZ_OPENAI_BASE_URL holds a user or password" in result.out
    assert "OpenAI: not checked: ANTIFAZ_OPENAI_BASE_URL holds a user or password" in result.out
    assert [str(r.url) for r in provider.requests] == ["https://api.anthropic.com/v1/models"]
    assert not result.holds_any([password, *canaries.all()])


def test_a_base_url_with_a_password_is_only_a_warning_without_providers(
    tmp_path: Path, canaries: Keys, gateway: Any, run: Any
) -> None:
    password = canary("pw")
    write_env(tmp_path, canaries, ANTIFAZ_ANTHROPIC_BASE_URL=f"https://{password}@fake.invalid")
    gateway(healthy())
    result = run()
    assert result.code == 0
    assert "ANTIFAZ_ANTHROPIC_BASE_URL holds a user or password" in result.out
    assert not result.holds_any([password, *canaries.all()])


def test_httpx_logs_nothing_about_provider_requests(
    tmp_path: Path, canaries: Keys, gateway: Any, providers: Any, run: Any
) -> None:
    marker = canary("host")
    write_env(tmp_path, canaries, ANTIFAZ_OPENAI_BASE_URL=f"https://{marker}.fake.invalid/v1")
    gateway(healthy())
    providers(models_ok)
    httpx_logger = logging.getLogger("httpx")
    before = httpx_logger.level
    httpx_logger.setLevel(logging.NOTSET)  # as a library user would leave it: INFO gets through
    try:
        result = run("--providers")
        after = httpx_logger.level
    finally:
        httpx_logger.setLevel(before)
    assert result.code == 0
    assert marker not in result.logs
    assert "HTTP Request" not in result.logs
    assert after == logging.NOTSET  # doctor puts the library loggers back as they were


# --- Gateway ------------------------------------------------------------------------------------


def test_an_unreachable_gateway_fails_with_a_hint(
    tmp_path: Path, canaries: Keys, gateway: Any, run: Any
) -> None:
    write_env(tmp_path, canaries)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    gateway(refuse)
    result = run()
    assert result.code == 1
    assert "gateway: cannot connect" in result.out
    assert "docker compose up -d" in result.out
    assert "connection refused" not in result.out


def test_a_gateway_timeout_fails(tmp_path: Path, canaries: Keys, gateway: Any, run: Any) -> None:
    write_env(tmp_path, canaries)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    gateway(slow)
    result = run("--timeout", "1.5")
    assert result.code == 1
    assert "gateway: no answer within 1.5 s" in result.out


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(503, json={"status": "ok"}), "did not answer ok (HTTP 503)"),
        (httpx.Response(200, json={"status": "down"}), "did not answer ok (HTTP 200)"),
        (httpx.Response(200, content=b"not json"), "did not answer ok (HTTP 200)"),
        (httpx.Response(200, json=["ok"]), "did not answer ok (HTTP 200)"),
        (httpx.Response(302, headers={"location": "/x"}), "did not answer ok (HTTP 302)"),
        (httpx.Response(400, text="Invalid host header"), "refused the Host header (HTTP 400)"),
    ],
)
def test_a_bad_healthz_answer_fails(
    tmp_path: Path, canaries: Keys, gateway: Any, run: Any, response: httpx.Response, message: str
) -> None:
    write_env(tmp_path, canaries)
    health = gateway(lambda request: response)
    result = run()
    assert result.code == 1
    assert message in result.out
    assert len(health.requests) == 1  # a redirect is not followed


def test_a_huge_healthz_answer_is_not_trusted(
    tmp_path: Path, canaries: Keys, gateway: Any, run: Any
) -> None:
    write_env(tmp_path, canaries)
    padding = "x" * (doctor.MAX_HEALTH_BYTES + 1)
    gateway(lambda request: httpx.Response(200, json={"status": "ok", "pad": padding}))
    assert run().code == 1


def test_an_odd_version_is_not_echoed(
    tmp_path: Path, canaries: Keys, gateway: Any, run: Any
) -> None:
    write_env(tmp_path, canaries)
    odd = "0.2.0 <script>" + canary("v")
    gateway(lambda request: httpx.Response(200, json={"status": "ok", "version": odd}))
    result = run()
    assert result.code == 0
    assert "version unknown" in result.out
    assert odd not in result.out


@pytest.mark.parametrize(
    ("ner", "code", "message"),
    [
        ("ok", 0, "gateway NER: ok"),
        ("starting", 0, "gateway NER: starting"),
        ("circuit_open", 1, "gateway NER: circuit_open"),
        ("closed", 1, "gateway NER: closed"),
        ("something else", 1, "gateway NER: unknown state"),
    ],
)
def test_the_ner_state_from_healthz(
    tmp_path: Path, canaries: Keys, gateway: Any, run: Any, ner: str, code: int, message: str
) -> None:
    write_env(tmp_path, canaries)
    gateway(healthy(ner))
    result = run()
    assert result.code == code
    assert message in result.out
    assert "something else" not in result.out


def test_ner_on_in_the_configuration_but_off_in_the_gateway_is_noted(
    tmp_path: Path, canaries: Keys, gateway: Any, run: Any
) -> None:
    write_env(tmp_path, canaries, ANTIFAZ_NER_ENABLED="true")
    gateway(healthy())
    result = run()
    assert result.code == 0
    assert "NER (names and addresses): on" in result.out
    assert "runs without it" in result.out


@pytest.mark.parametrize(
    ("url", "allowed", "expected"),
    [
        ("http://127.0.0.1:8000", ["localhost", "127.0.0.1"], "127.0.0.1"),
        ("http://localhost:8000", ["LOCALHOST"], "localhost"),
        ("http://antifaz:8000", ["localhost", "127.0.0.1", "[::1]"], "localhost"),
        ("http://[::1]:8000", ["localhost", "[::1]"], "[::1]"),
        ("http://127.0.0.1:8000", ["*.example.com", "gw.example.com"], "gw.example.com"),
        ("http://127.0.0.1:8000", ["*.example.com"], "healthcheck.example.com"),
        ("https://a.example.com", ["*.example.com"], "a.example.com"),
        ("http://127.0.0.1:8000", None, "127.0.0.1"),
    ],
)
def test_host_choice(url: str, allowed: list[str] | None, expected: str) -> None:
    hosts = [host.lower() for host in allowed] if allowed is not None else None
    assert doctor.choose_host(url, hosts) == expected


# --- Configuration ------------------------------------------------------------------------------


def test_a_missing_gateway_key_fails_naming_the_variable(
    tmp_path: Path, canaries: Keys, gateway: Any, run: Any
) -> None:
    write_env(tmp_path, canaries, ANTIFAZ_API_KEY="")
    gateway(healthy())
    result = run()
    assert result.code == 1
    assert "configuration: the gateway would not start: ANTIFAZ_API_KEY is missing" in result.out
    assert not result.holds_any(canaries.all())


def test_an_example_key_fails_without_printing_it(
    tmp_path: Path, canaries: Keys, gateway: Any, run: Any
) -> None:
    example = "change-me-" + secrets.token_hex(16)
    write_env(tmp_path, canaries, ANTIFAZ_OPENAI_API_KEY=example)
    gateway(healthy())
    result = run()
    assert result.code == 1
    assert "ANTIFAZ_OPENAI_API_KEY still has the example value" in result.out
    assert not result.holds_any([example, *canaries.all()])


def test_a_value_that_cannot_be_read_is_named_not_shown(
    tmp_path: Path, canaries: Keys, gateway: Any, run: Any
) -> None:
    bad = "many-" + secrets.token_hex(8)
    write_env(tmp_path, canaries, ANTIFAZ_NER_WORKERS=bad)
    gateway(healthy())
    result = run("--providers")
    assert result.code == 1
    assert "configuration: cannot read; check ANTIFAZ_NER_WORKERS" in result.out
    assert "providers: not checked" in result.out
    assert not result.holds_any([bad, *canaries.all()])


def test_a_provider_key_with_a_bad_format_is_not_sent(
    tmp_path: Path,
    canaries: Keys,
    gateway: Any,
    providers: Any,
    monkeypatch: pytest.MonkeyPatch,
    run: Any,
) -> None:
    write_env(tmp_path, canaries)
    gateway(healthy())
    provider = providers(models_ok)
    bad = "bad key " + canary("sk-")
    monkeypatch.setenv("ANTIFAZ_OPENAI_API_KEY", bad)  # the environment wins over .env
    result = run("--providers")
    assert result.code == 1
    assert "OpenAI: not checked: fix ANTIFAZ_OPENAI_API_KEY first" in result.out
    assert [str(r.url) for r in provider.requests] == ["https://api.anthropic.com/v1/models"]
    assert not result.holds_any([bad, *canaries.all()])


def test_environment_variables_override_the_env_file(
    tmp_path: Path, canaries: Keys, gateway: Any, monkeypatch: pytest.MonkeyPatch, run: Any
) -> None:
    write_env(tmp_path, canaries, ANTIFAZ_API_KEY="")
    monkeypatch.setenv("ANTIFAZ_API_KEY", canaries.gateway)
    gateway(healthy())
    assert run().code == 0


def test_without_env_file_only_the_environment_is_read(
    canaries: Keys, gateway: Any, monkeypatch: pytest.MonkeyPatch, run: Any
) -> None:
    monkeypatch.setenv("ANTIFAZ_API_KEY", canaries.gateway)
    gateway(healthy())
    result = run()
    assert result.code == 0
    assert "no .env in that folder" in result.out
    assert "providers with a key: none" in result.out


def test_path_reads_the_env_of_another_folder(
    tmp_path: Path, canaries: Keys, gateway: Any, run: Any
) -> None:
    other = tmp_path / "srv"
    other.mkdir()
    write_env(other, canaries)
    gateway(healthy())
    assert run().code == 1  # the current folder has no .env
    assert run("--path", str(other)).code == 0


# --- Usage --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["--url", "ftp://127.0.0.1:8000"],
        ["--url", "127.0.0.1:8000"],
        ["--url", "http://127.0.0.1:99999"],
        ["--url", "http://127.0.0.1:8000/?x=1"],
        ["--url", "http://"],
    ],
)
def test_a_bad_url_is_a_usage_error(gateway: Any, run: Any, args: list[str]) -> None:
    health = gateway(healthy())
    result = run(*args)
    assert result.code == 2
    assert health.requests == []


def test_a_url_with_a_password_is_refused_without_echoing_it(gateway: Any, run: Any) -> None:
    password = canary("pw")
    health = gateway(healthy())
    result = run("--url", f"http://user:{password}@127.0.0.1:8000")
    assert result.code == 2
    assert health.requests == []
    assert not result.holds_any([password])


def test_a_missing_folder_is_a_usage_error(tmp_path: Path, run: Any) -> None:
    assert run("--path", str(tmp_path / "nope")).code == 2


@pytest.mark.parametrize("timeout", ["0", "-1", "abc", "121", "nan"])
def test_a_bad_timeout_is_a_usage_error(timeout: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["doctor", "--timeout", timeout])
    assert exit_info.value.code == 2
    assert "invalid arguments" in capsys.readouterr().err


# --- Output -------------------------------------------------------------------------------------


def _console(encoding: str) -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding)


def test_marks_fall_back_to_ascii_on_a_cp1252_console() -> None:
    assert doctor.marks_for(_console("cp1252")) == doctor.ASCII_MARKS
    assert doctor.marks_for(_console("ascii")) == doctor.ASCII_MARKS
    assert doctor.marks_for(_console("utf-8")) == doctor.MARKS


def test_the_report_prints_on_a_cp1252_console(
    tmp_path: Path, canaries: Keys, gateway: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_env(tmp_path, canaries, ANTIFAZ_API_KEY="")
    gateway(healthy())
    console = _console("cp1252")
    monkeypatch.setattr("sys.stdout", console)
    assert main(["doctor"]) == 1
    console.flush()
    text = console.buffer.getvalue().decode("cp1252")  # type: ignore[attr-defined]
    assert "OK   gateway: up" in text
    assert "FAIL configuration" in text
    assert text.isascii()


def test_the_report_uses_check_marks_on_a_utf8_console(
    tmp_path: Path, canaries: Keys, gateway: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_env(tmp_path, canaries)
    gateway(healthy())
    console = _console("utf-8")
    monkeypatch.setattr("sys.stdout", console)
    assert main(["doctor"]) == 0
    console.flush()
    text = console.buffer.getvalue().decode("utf-8")  # type: ignore[attr-defined]
    assert "✓ gateway: up" in text


def test_help_lists_doctor(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "doctor" in capsys.readouterr().out


# --- Proxies, with real sockets on 127.0.0.1 ------------------------------------------------------


@dataclass
class Server:
    url: str
    requests: list[tuple[str, dict[str, str]]]


@pytest.fixture
def start_server() -> Iterator[Callable[[int, bytes], Server]]:
    """Tiny HTTP servers on 127.0.0.1 that record each request line and headers."""
    servers: list[ThreadingHTTPServer] = []

    def start(status: int, body: bytes) -> Server:
        seen: list[tuple[str, dict[str, str]]] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                """Silent."""

            def do_GET(self) -> None:
                seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return Server(f"http://127.0.0.1:{server.server_address[1]}", seen)

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _set_proxy(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, url)


def test_healthz_never_goes_through_a_proxy(
    tmp_path: Path,
    canaries: Keys,
    start_server: Callable[[int, bytes], Server],
    monkeypatch: pytest.MonkeyPatch,
    run: Any,
) -> None:
    write_env(tmp_path, canaries)
    health = start_server(200, b'{"status": "ok", "version": "0.2.0"}')
    proxy = start_server(502, b"{}")
    _set_proxy(monkeypatch, proxy.url)
    result = run("--url", health.url)
    assert result.code == 0, result.out
    assert proxy.requests == []
    assert [path for path, _ in health.requests] == ["/healthz"]
    headers = health.requests[0][1]
    assert headers["host"] == "127.0.0.1"
    assert "authorization" not in headers and "x-api-key" not in headers


def test_provider_checks_honour_the_proxy_like_the_gateway(
    tmp_path: Path,
    canaries: Keys,
    start_server: Callable[[int, bytes], Server],
    monkeypatch: pytest.MonkeyPatch,
    run: Any,
) -> None:
    health = start_server(200, b'{"status": "ok", "version": "0.2.0"}')
    upstream = start_server(200, b'{"data": []}')
    proxy = start_server(200, b'{"data": []}')
    write_env(
        tmp_path,
        canaries,
        ANTIFAZ_ANTHROPIC_API_KEY="",
        ANTIFAZ_OPENAI_BASE_URL=f"{upstream.url}/v1",
    )
    _set_proxy(monkeypatch, proxy.url)
    result = run("--url", health.url, "--providers")
    assert result.code == 0, result.out
    assert proxy.requests and upstream.requests == []  # through the proxy, as the gateway goes
    assert proxy.requests[0][0] == f"{upstream.url}/v1/models"
    assert health.requests  # and the gateway check went direct


# --- Total deadlines, encodings and unreadable files -------------------------------------------


def test_a_healthz_compressed_answer_is_refused(
    tmp_path: Path, canaries: Keys, gateway: Any, run: Any
) -> None:
    write_env(tmp_path, canaries)
    body = gzip.compress(b'{"status": "ok", "version": "0.2.0"}')
    health = gateway(
        lambda request: httpx.Response(200, content=body, headers={"content-encoding": "gzip"})
    )
    result = run()
    assert result.code == 1
    assert "gateway: /healthz answer is compressed" in result.out
    assert health.requests[0].headers["accept-encoding"] == "identity"


def test_a_healthz_identity_encoding_is_accepted(
    tmp_path: Path, canaries: Keys, gateway: Any, run: Any
) -> None:
    write_env(tmp_path, canaries)
    gateway(
        lambda request: httpx.Response(
            200, json={"status": "ok", "version": "0.2.0"}, headers={"content-encoding": "identity"}
        )
    )
    assert run().code == 0


def test_an_env_file_that_is_not_utf8_is_named_not_shown(
    tmp_path: Path, canaries: Keys, gateway: Any, run: Any
) -> None:
    text = f"ANTIFAZ_API_KEY={canaries.gateway}\nANTIFAZ_APP_NAME=compañía\n"
    (tmp_path / ".env").write_bytes(text.encode("cp1252"))
    gateway(healthy())
    result = run("--providers")
    assert result.code == 1
    assert "configuration: cannot read .env as UTF-8 text" in result.out
    assert "providers: not checked" in result.out
    assert not result.holds_any(canaries.all())
    assert "Traceback" not in result.err


def test_an_env_file_that_cannot_be_opened_is_reported(
    tmp_path: Path, canaries: Keys, gateway: Any, monkeypatch: pytest.MonkeyPatch, run: Any
) -> None:
    write_env(tmp_path, canaries)
    gateway(healthy())

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError("denied")

    monkeypatch.setattr(doctor, "Settings", broken)
    result = run()
    assert result.code == 1
    assert "configuration: cannot read .env as UTF-8 text" in result.out


@pytest.fixture
def drip_server() -> Iterator[Callable[[bool], str]]:
    """A server on 127.0.0.1 that sends one byte every 0.1 s for up to 10 s, in the headers
    (`in_headers`) or in the body. Each byte arrives well within httpx's read timeout."""
    servers: list[ThreadingHTTPServer] = []

    def start(in_headers: bool) -> str:
        class Drip(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                """Silent."""

            def do_GET(self) -> None:
                head = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                try:
                    if in_headers:
                        self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                        for _ in range(100):
                            self.wfile.write(b"X-Slow: a\r\n")
                            self.wfile.flush()
                            time.sleep(0.1)
                        return
                    self.wfile.write(head + b"Content-Length: 1000\r\n\r\n")
                    for _ in range(100):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(0.1)
                except OSError:
                    return  # the client gave up, as it should

        server = ThreadingHTTPServer(("127.0.0.1", 0), Drip)
        server.daemon_threads = True
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("in_headers", [False, True])
def test_the_timeout_is_a_total_deadline_for_healthz(
    tmp_path: Path, canaries: Keys, drip_server: Callable[[bool], str], run: Any, in_headers: bool
) -> None:
    write_env(tmp_path, canaries)
    url = drip_server(in_headers)
    started = time.monotonic()
    result = run("--url", url, "--timeout", "1")
    elapsed = time.monotonic() - started
    assert result.code == 1
    assert "gateway: no answer within 1 s" in result.out
    assert elapsed < 4


@pytest.mark.parametrize("in_headers", [False, True])
def test_the_timeout_is_a_total_deadline_for_providers(
    tmp_path: Path,
    canaries: Keys,
    gateway: Any,
    drip_server: Callable[[bool], str],
    run: Any,
    in_headers: bool,
) -> None:
    url = drip_server(in_headers)
    write_env(tmp_path, canaries, ANTIFAZ_ANTHROPIC_API_KEY="", ANTIFAZ_OPENAI_BASE_URL=url)
    gateway(healthy())
    started = time.monotonic()
    result = run("--providers", "--timeout", "1")
    elapsed = time.monotonic() - started
    assert result.code == (1 if in_headers else 0)
    if in_headers:
        assert "OpenAI: no answer within 1 s" in result.out
    else:  # the status line came in time; the body is never read
        assert "OpenAI: the key works" in result.out
    assert elapsed < 4


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://api.openai.com/v1", False),
        ("https://user:pw@api.example.com/v1", True),
        ("https://token@api.example.com", True),
        ("https://api.example.com/v1?a=b@c", False),
        ("http://[::1", False),  # not a URL at all: the request itself will fail, generically
    ],
)
def test_userinfo_detection(url: str, expected: bool) -> None:
    assert doctor.has_userinfo(url) is expected
