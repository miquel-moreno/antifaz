"""`antifaz db migrate` and `antifaz db status` without PostgreSQL, and doctor's PostgreSQL
line (issue 121, PR 8b-1, decisions D7 and D8).

Settings come from environment variables only (no .env is ever written here). Exit codes as in
doctor: 0 fine, 1 the database has a problem, 2 usage or configuration. Every message is fixed
text that names variables, never values; usage errors never repeat the arguments.
"""

import logging
import os
import secrets
import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from antifaz.cli import doctor, main
from tests.conftest import forbid_network


def canary(prefix: str) -> str:
    return f"{prefix}-canary-{secrets.token_hex(12)}"


@dataclass
class Result:
    code: int
    out: str
    err: str
    logs: str

    @property
    def everything(self) -> str:
        return self.out + self.err + self.logs


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in list(os.environ):
        if name.upper().startswith(("ANTIFAZ_", "PG")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # an empty folder: no .env
    forbid_network(monkeypatch)


@pytest.fixture
def run(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> Callable[..., Result]:
    def _run(*args: str) -> Result:
        caplog.set_level(logging.DEBUG)
        try:
            code = main(list(args))
        except SystemExit as stop:
            code = int(stop.code or 0)
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err, caplog.text)

    return _run


def closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def configure(monkeypatch: pytest.MonkeyPatch, password: str, **extra: str) -> None:
    values = {
        "ANTIFAZ_DATABASE_HOST": "127.0.0.1",
        "ANTIFAZ_DATABASE_PORT": str(closed_port()),
        "ANTIFAZ_DATABASE_PASSWORD": password,
        "ANTIFAZ_DATABASE_SSLMODE": "disable",
        "ANTIFAZ_DATABASE_TIMEOUT_SECONDS": "15",
    }
    for name, value in (values | extra).items():
        monkeypatch.setenv(name, value)


# --- Parser -----------------------------------------------------------------------------------


def test_db_help_lists_migrate_and_status(run: Any) -> None:
    result = run("db", "--help")

    assert result.code == 0
    assert "migrate" in result.out
    assert "status" in result.out


@pytest.mark.parametrize("command", ["migrate", "status"])
def test_each_db_command_has_help(run: Any, command: str) -> None:
    result = run("db", command, "--help")

    assert result.code == 0
    assert "--path" in result.out


@pytest.mark.parametrize(
    "args",
    [
        ("db",),
        ("db", "drop"),
        ("db", "status", "{secret}"),
        ("db", "migrate", "--password", "{secret}"),
        ("db", "status", "--path"),
    ],
)
def test_usage_errors_are_generic_and_never_repeat_the_arguments(
    run: Any, args: tuple[str, ...]
) -> None:
    secret = canary("arg")
    result = run(*(arg.format(secret=secret) for arg in args))

    assert result.code == 2
    assert "invalid arguments" in result.err
    assert secret not in result.everything


# --- Configuration ----------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["migrate", "status"])
def test_without_a_database_it_says_so_and_exits_2(run: Any, command: str) -> None:
    result = run("db", command)

    assert result.code == 2
    assert result.out == ""
    assert result.err == (
        "antifaz db: PostgreSQL is not configured: set ANTIFAZ_DATABASE_HOST and "
        "ANTIFAZ_DATABASE_PASSWORD (virtual keys are off without it)\n"
    )


@pytest.mark.parametrize("command", ["migrate", "status"])
def test_a_bad_database_setting_is_named_and_exits_2(
    monkeypatch: pytest.MonkeyPatch, run: Any, command: str
) -> None:
    configure(monkeypatch, "change-me-database")

    result = run("db", command)

    assert result.code == 2
    assert "ANTIFAZ_DATABASE_PASSWORD" in result.err
    assert "change-me-database" not in result.everything


def test_an_unreadable_setting_is_named_without_its_value(
    monkeypatch: pytest.MonkeyPatch, run: Any
) -> None:
    password = canary("pw")
    configure(monkeypatch, canary("ok"), ANTIFAZ_DATABASE_PORT=password)

    result = run("db", "status")

    assert result.code == 2
    assert "ANTIFAZ_DATABASE_PORT" in result.err
    assert password not in result.everything


def test_a_missing_path_exits_2(run: Any, tmp_path: Path) -> None:
    result = run("db", "status", "--path", str(tmp_path / "nowhere"))

    assert result.code == 2
    assert "--path" in result.err


# --- A database that does not answer ----------------------------------------------------------


@pytest.mark.parametrize("command", ["migrate", "status"])
def test_an_unreachable_database_exits_1_with_a_fixed_text(
    monkeypatch: pytest.MonkeyPatch, run: Any, command: str
) -> None:
    from antifaz.db import PROBLEM_TEXTS, DatabaseProblem

    password = canary("pw")
    configure(monkeypatch, password)

    result = run("db", command)

    assert result.code == 1
    assert PROBLEM_TEXTS[DatabaseProblem.UNREACHABLE] in result.err
    assert "unreachable" in result.err
    assert password not in result.everything


# --- doctor ------------------------------------------------------------------------------------


@pytest.fixture
def healthy_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"status": "ok", "version": "0.2.0"}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    monkeypatch.setattr(doctor, "_gateway_transport", lambda: transport)
    monkeypatch.setenv("ANTIFAZ_API_KEY", secrets.token_hex(32))


def test_doctor_says_postgresql_is_off_without_a_host(healthy_gateway: None, run: Any) -> None:
    result = run("doctor")

    assert "- PostgreSQL: not configured (virtual keys off)" in result.out
    assert result.code == 0


def test_doctor_points_to_db_status_when_postgresql_is_configured(
    monkeypatch: pytest.MonkeyPatch, healthy_gateway: None, run: Any
) -> None:
    password = canary("pw")
    configure(monkeypatch, password)

    result = run("doctor")

    assert "- PostgreSQL: configured; check it with `antifaz db status`" in result.out
    assert result.code == 0
    assert password not in result.everything


def test_doctor_names_a_bad_database_setting_as_a_failure(
    monkeypatch: pytest.MonkeyPatch, healthy_gateway: None, run: Any
) -> None:
    configure(monkeypatch, "change-me", ANTIFAZ_DATABASE_HOST="user@db.internal")

    result = run("doctor")

    assert result.code == 1
    assert "PostgreSQL: ANTIFAZ_DATABASE_" in result.out
    assert "user@db.internal" not in result.everything
    assert "change-me" not in result.everything


TLS_OFF = "PostgreSQL: TLS off (ANTIFAZ_DATABASE_SSLMODE=disable)"


@pytest.mark.parametrize("host", ["db.internal", "10.0.0.5", "192.168.1.20", "fd00::5"])
def test_doctor_warns_when_tls_is_off_for_a_remote_host(
    monkeypatch: pytest.MonkeyPatch, healthy_gateway: None, run: Any, host: str
) -> None:
    configure(monkeypatch, canary("pw"), ANTIFAZ_DATABASE_HOST=host)

    result = run("doctor")

    assert TLS_OFF in result.out
    assert "warning:" in result.out.split(TLS_OFF)[0].splitlines()[-1]
    assert host not in result.out
    assert result.code == 0  # a warning, not a failure


@pytest.mark.parametrize("host", ["127.0.0.1", "127.8.9.10", "::1", "localhost", "LOCALHOST"])
def test_doctor_does_not_warn_for_a_loopback_host(
    monkeypatch: pytest.MonkeyPatch, healthy_gateway: None, run: Any, host: str
) -> None:
    configure(monkeypatch, canary("pw"), ANTIFAZ_DATABASE_HOST=host)

    assert TLS_OFF not in run("doctor").out


def test_doctor_does_not_warn_with_verify_full(
    monkeypatch: pytest.MonkeyPatch, healthy_gateway: None, run: Any
) -> None:
    configure(
        monkeypatch,
        canary("pw"),
        ANTIFAZ_DATABASE_HOST="db.internal",
        ANTIFAZ_DATABASE_SSLMODE="verify-full",
    )

    assert TLS_OFF not in run("doctor").out


def test_db_status_warns_when_tls_is_off_for_a_remote_host(
    monkeypatch: pytest.MonkeyPatch, run: Any
) -> None:
    """Printed before connecting (here the connection itself is refused by the test guard)."""
    configure(monkeypatch, canary("pw"), ANTIFAZ_DATABASE_HOST="db.internal")

    result = run("db", "status")

    assert TLS_OFF in result.err
    assert "db.internal" not in result.everything


def test_db_status_does_not_warn_for_a_loopback_host(
    monkeypatch: pytest.MonkeyPatch, run: Any
) -> None:
    configure(monkeypatch, canary("pw"))

    assert TLS_OFF not in run("db", "status").err
