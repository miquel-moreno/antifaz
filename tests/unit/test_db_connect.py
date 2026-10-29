"""Connecting to PostgreSQL, without PostgreSQL (issue 121, PR 8b-1, ADR-0021).

Fake servers on 127.0.0.1 (the forbid_network guard allows only loopback) speak just enough of
the PostgreSQL protocol: a closed port, a mute server, a server that speaks HTTP, one that asks
for a password and refuses it, and one that refuses TLS. Every failure is a closed reason with a
fixed text: never asyncpg's message, which can hold the user, the database, the host or the
server's own words. A canary password must not reach the error (str, repr, args, cause,
context, formatted traceback, the locals of its frames) nor any log, asyncpg's included.
The libpq variables (PGHOST, PGPASSWORD, PGSSLMODE...) change nothing.
"""

import asyncio
import contextlib
import gc
import logging
import os
import secrets
import socket
import struct
import time
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

import pytest
from pydantic import SecretStr

from antifaz.config import Settings
from antifaz.db import PROBLEM_TEXTS, DatabaseError, DatabaseProblem, connect
from tests.conftest import forbid_network

SSL_REQUEST = 80877103
PROTOCOL_3 = 196608


def canary(prefix: str) -> str:
    return f"{prefix}-canary-{secrets.token_hex(12)}"


@dataclass
class Canaries:
    password: str = field(default_factory=lambda: canary("password"))
    user: str = field(default_factory=lambda: canary("user"))
    database: str = field(default_factory=lambda: canary("db"))
    server_words: str = field(default_factory=lambda: canary("server"))
    environment: str = field(default_factory=lambda: canary("pgenv"))

    def all(self) -> list[str]:
        return [self.password, self.user, self.database, self.server_words, self.environment]


@pytest.fixture
def canaries() -> Canaries:
    return Canaries()


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, canaries: Canaries) -> None:
    """Loopback only, and libpq variables pointing somewhere else with other credentials."""
    for name in list(os.environ):
        if name.upper().startswith(("ANTIFAZ_", "PG")):
            monkeypatch.delenv(name, raising=False)
    forbid_network(monkeypatch)
    monkeypatch.setenv("PGHOST", "127.0.0.2")
    monkeypatch.setenv("PGPORT", "1")
    monkeypatch.setenv("PGUSER", canaries.environment)
    monkeypatch.setenv("PGPASSWORD", canaries.environment)
    monkeypatch.setenv("PGDATABASE", canaries.environment)
    monkeypatch.setenv("PGSSLMODE", "require")
    monkeypatch.setenv("PGTARGETSESSIONATTRS", "read-only")
    monkeypatch.setenv("PGSSLNEGOTIATION", "direct")


def settings(port: int, canaries: Canaries, **values: object) -> Settings:
    base: dict[str, object] = {
        "database_host": "127.0.0.1",
        "database_port": port,
        "database_name": canaries.database,
        "database_user": canaries.user,
        "database_password": SecretStr(canaries.password),
        "database_sslmode": "disable",
        "database_timeout_seconds": 2.0,
    }
    base |= values
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


# --- Fake servers ------------------------------------------------------------------------------


@dataclass
class Seen:
    """What a fake server received."""

    first_codes: list[int] = field(default_factory=list)
    parameters: dict[str, str] = field(default_factory=dict)
    password: str | None = None


Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter, Seen], Awaitable[None]]


@contextlib.asynccontextmanager
async def server(handler: Handler) -> AsyncIterator[tuple[int, Seen]]:
    seen = Seen()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            await handler(reader, writer, seen)
        with contextlib.suppress(Exception):
            writer.close()

    listening = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = listening.sockets[0].getsockname()[1]
    try:
        yield port, seen
    finally:
        listening.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(listening.wait_closed(), 2)


async def read_startup(reader: asyncio.StreamReader, seen: Seen, ssl_answer: bytes) -> bool:
    """Read the startup message (answering an SSLRequest first). False if TLS was refused."""
    while True:
        (length,) = struct.unpack("!i", await reader.readexactly(4))
        body = await reader.readexactly(length - 4)
        (code,) = struct.unpack("!i", body[:4])
        seen.first_codes.append(code)
        if code != SSL_REQUEST:
            break
        return_writer = getattr(reader, "_antifaz_writer", None)
        assert return_writer is not None
        return_writer.write(ssl_answer)
        await return_writer.drain()
        if ssl_answer == b"N":
            return False
    fields = body[4:].split(b"\x00")
    seen.parameters = {
        fields[i].decode(): fields[i + 1].decode() for i in range(0, len(fields) - 2, 2)
    }
    return True


def attach(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    reader._antifaz_writer = writer  # type: ignore[attr-defined]


def error_response(code: str, message: str) -> bytes:
    fields = b"".join(
        kind + text.encode() + b"\x00"
        for kind, text in ((b"S", "FATAL"), (b"V", "FATAL"), (b"C", code), (b"M", message))
    )
    body = fields + b"\x00"
    return b"E" + struct.pack("!i", len(body) + 4) + body


def refusing_password(canaries: Canaries) -> Handler:
    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter, seen: Seen
    ) -> None:
        attach(reader, writer)
        await read_startup(reader, seen, b"N")
        writer.write(b"R" + struct.pack("!ii", 8, 3))  # AuthenticationCleartextPassword
        await writer.drain()
        kind = await reader.readexactly(1)
        assert kind == b"p"
        (length,) = struct.unpack("!i", await reader.readexactly(4))
        seen.password = (await reader.readexactly(length - 4)).rstrip(b"\x00").decode()
        user = seen.parameters.get("user", "")
        words = f'password authentication failed for user "{user}" {canaries.server_words}'
        writer.write(error_response("28P01", words))
        await writer.drain()
        await reader.read()

    return handle


async def mute(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, seen: Seen) -> None:
    await asyncio.sleep(30)


async def http_speaker(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, seen: Seen
) -> None:
    await reader.read(8)
    writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n" * 4)
    await writer.drain()
    await asyncio.sleep(30)


async def refusing_tls(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, seen: Seen
) -> None:
    attach(reader, writer)
    await read_startup(reader, seen, b"N")
    await reader.read()


def closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


# --- Checks of every failure -------------------------------------------------------------------


def frame_locals(error: BaseException) -> list[str]:
    texts: list[str] = []
    tb = error.__traceback__
    while tb is not None:
        texts += [repr(value) for value in tb.tb_frame.f_locals.values()]
        tb = tb.tb_next
    return texts


def assert_closed(error: DatabaseError, problem: DatabaseProblem, canaries: Canaries) -> None:
    assert error.problem is problem
    assert str(error) == PROBLEM_TEXTS[problem]
    assert error.args == (PROBLEM_TEXTS[problem],)
    assert error.__cause__ is None
    assert error.__context__ is None
    shown = [str(error), repr(error), "".join(traceback.format_exception(error))]
    for secret in canaries.all():
        assert not any(secret in text for text in shown), secret
    # The settings (user and database name included) may sit in a frame; the password never.
    assert not any(canaries.password in text for text in frame_locals(error))


async def failure(
    configured: Settings, caplog: pytest.LogCaptureFixture
) -> tuple[DatabaseError, float]:
    caplog.set_level(logging.DEBUG)
    logging.getLogger("asyncpg").setLevel(logging.DEBUG)
    started = time.monotonic()
    with pytest.raises(DatabaseError) as caught:
        await connect(configured)
    gc.collect()
    return caught.value, time.monotonic() - started


def assert_no_logs(caplog: pytest.LogCaptureFixture, canaries: Canaries) -> None:
    for secret in canaries.all():
        assert secret not in caplog.text


# --- Tests ------------------------------------------------------------------------------------


def test_every_problem_has_a_fixed_text() -> None:
    assert set(PROBLEM_TEXTS) == set(DatabaseProblem)
    assert {problem.value for problem in DatabaseProblem} == {
        "unreachable",
        "timeout",
        "auth_failed",
        "tls_failed",
        "too_old",
        "not_utf8",
        "not_migrated",
        "behind",
        "ahead",
        "migration_changed",
        "other",
    }
    assert all(text and "{" not in text for text in PROBLEM_TEXTS.values())


def test_connecting_needs_a_configured_database(canaries: Canaries) -> None:
    configured = Settings(_env_file=None)  # type: ignore[call-arg]

    with pytest.raises(ValueError, match="ANTIFAZ_DATABASE_HOST"):
        asyncio.run(connect(configured))


async def test_a_closed_port_is_unreachable(
    canaries: Canaries, caplog: pytest.LogCaptureFixture
) -> None:
    # Windows waits about 2 s before it reports a refused loopback connection.
    error, _ = await failure(settings(closed_port(), canaries, database_timeout_seconds=15), caplog)

    assert_closed(error, DatabaseProblem.UNREACHABLE, canaries)
    assert_no_logs(caplog, canaries)


async def test_a_mute_server_times_out_within_the_limit(
    canaries: Canaries, caplog: pytest.LogCaptureFixture
) -> None:
    async with server(mute) as (port, _):
        error, elapsed = await failure(
            settings(port, canaries, database_timeout_seconds=0.5), caplog
        )

    assert_closed(error, DatabaseProblem.TIMEOUT, canaries)
    assert elapsed < 0.5 + 2.0
    assert_no_logs(caplog, canaries)


async def test_a_server_that_is_not_postgresql_is_other(
    canaries: Canaries, caplog: pytest.LogCaptureFixture
) -> None:
    async with server(http_speaker) as (port, _):
        error, _ = await failure(settings(port, canaries), caplog)

    assert_closed(error, DatabaseProblem.OTHER, canaries)
    assert_no_logs(caplog, canaries)


async def test_a_refused_password_is_auth_failed_without_the_server_words(
    canaries: Canaries, caplog: pytest.LogCaptureFixture
) -> None:
    async with server(refusing_password(canaries)) as (port, seen):
        error, _ = await failure(settings(port, canaries), caplog)

    assert_closed(error, DatabaseProblem.AUTH_FAILED, canaries)
    assert_no_logs(caplog, canaries)
    assert seen.password == canaries.password  # the server did get the configured one


async def test_the_libpq_variables_change_nothing(
    canaries: Canaries, caplog: pytest.LogCaptureFixture
) -> None:
    """PGHOST, PGUSER, PGPASSWORD, PGDATABASE, PGSSLMODE... are set to other values."""
    async with server(refusing_password(canaries)) as (port, seen):
        await failure(settings(port, canaries), caplog)

    assert seen.first_codes == [PROTOCOL_3]  # sslmode disable: no SSLRequest despite PGSSLMODE
    assert seen.parameters["user"] == canaries.user
    assert seen.parameters["database"] == canaries.database
    assert seen.password == canaries.password


async def test_the_session_settings_are_sent_at_startup(
    canaries: Canaries, caplog: pytest.LogCaptureFixture
) -> None:
    async with server(refusing_password(canaries)) as (port, seen):
        await failure(settings(port, canaries, database_timeout_seconds=3), caplog)

    assert seen.parameters["application_name"] == "antifaz"
    assert seen.parameters["statement_timeout"] == "3000"
    assert seen.parameters["lock_timeout"] == "3000"
    assert seen.parameters["idle_in_transaction_session_timeout"] == "3000"
    assert seen.parameters["log_parameter_max_length_on_error"] == "0"


async def test_verify_full_refuses_a_server_without_tls(
    canaries: Canaries, caplog: pytest.LogCaptureFixture
) -> None:
    async with server(refusing_tls) as (port, seen):
        error, _ = await failure(settings(port, canaries, database_sslmode="verify-full"), caplog)

    assert_closed(error, DatabaseProblem.TLS_FAILED, canaries)
    assert seen.first_codes == [SSL_REQUEST]  # asked for TLS first, never sent the password
    assert seen.password is None
    assert_no_logs(caplog, canaries)


def test_the_tls_context_verifies_the_certificate_and_the_host_name() -> None:
    import ssl

    from antifaz.db.connect import tls_context

    context = tls_context("verify-full")

    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert tls_context("disable") is False
