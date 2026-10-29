"""Connecting to PostgreSQL (issue 121, PR 8b-1, ADR-0021): the only module that imports asyncpg.

asyncpg ships no type hints, so the rest of antifaz.db talks to a small typed adapter, Session,
which passes `timeout=` on EVERY call and turns every failure into a DatabaseError with a closed
reason (DatabaseProblem) and a fixed text.

- connect(settings) uses the separate ANTIFAZ_DATABASE_* settings, never a DSN, and passes the
  host, port, user, password, database and TLS explicitly, so libpq's environment (PGHOST,
  PGUSER, PGPASSWORD, PGSSLMODE, PGSSLNEGOTIATION, PGTARGETSESSIONATTRS...), ~/.pgpass and the
  OS user name are never used. TLS: `verify-full` is an ssl.SSLContext that checks the
  certificate against the system's CAs and the host name (TLS 1.2 or newer); `disable` is no
  TLS at all. There is no "prefer": a server that refuses TLS is tls_failed, never plain text.
  OpenSSL also trusts the CAs named by SSL_CERT_FILE and SSL_CERT_DIR (8f adds a variable for
  an own CA). The settings are checked first (check_database_settings): a bad host or
  password never reaches asyncpg.
- Every session sets statement_timeout, lock_timeout and idle_in_transaction_session_timeout
  to ANTIFAZ_DATABASE_TIMEOUT_SECONDS, application_name=antifaz, and
  log_parameter_max_length_on_error=0 (PostgreSQL 16 lets any role set it for its own session,
  `pg_settings.context = 'user'`). That only keeps the $n arguments out of the server log's
  STATEMENT line of a failing statement. It does NOT hide the DETAIL line of a CHECK or UNIQUE
  that fails, which writes the row's values ("Failing row contains (...)", "Key (pepper_version,
  key_hash)=(...) already exists"); only `log_error_verbosity = terse` in postgresql.conf
  (superuser) drops it, and the admin should set it. In 8b-1 nothing writes keys yet; the 8b-2
  store checks every value in Python before writing, so a row the table refuses is a bug.
- Queries are Sql values: module-level constants written as `Sql("...")`; values travel as
  arguments ($1...). tests/unit/test_db_sql_rules.py enforces it from the AST.

Errors: the message of asyncpg or of the server can hold the user, the database, the host, the
server's own words or the values of a statement, so it is never shown, logged or chained. The
original error is classified, the locals of its traceback frames are cleared (the password sits
in asyncpg's frames) and the DatabaseError is raised OUTSIDE the except block, so it has no
__cause__ and no __context__. Its text is the fixed one of PROBLEM_TEXTS.

Forbidden: a DSN, PG* variables or a passfile; logging or printing an asyncpg error; building a
query with f-strings, % or .format().
"""

import ssl
import sys
import traceback
from collections.abc import Awaitable
from enum import StrEnum
from typing import Any, Literal, NewType, NoReturn

import asyncpg

from antifaz.config import Settings, check_database_settings

Sql = NewType("Sql", str)
APPLICATION_NAME = "antifaz"
# Kerberos/GSSAPI is never configured by Antifaz; these only stop PGKRBSRVNAME and PGGSSLIB from
# being read (a bad PGGSSLIB value would even make asyncpg refuse to connect).
_KERBEROS_SERVICE = "postgres"
_GSS_LIBRARY = "sspi" if sys.platform == "win32" else "gssapi"


class DatabaseProblem(StrEnum):
    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    AUTH_FAILED = "auth_failed"
    TLS_FAILED = "tls_failed"
    TOO_OLD = "too_old"
    NOT_UTF8 = "not_utf8"
    NOT_MIGRATED = "not_migrated"
    BEHIND = "behind"
    AHEAD = "ahead"
    MIGRATION_CHANGED = "migration_changed"
    OTHER = "other"


# Fixed texts: they name variables and commands, never a value.
PROBLEM_TEXTS: dict[DatabaseProblem, str] = {
    DatabaseProblem.UNREACHABLE: (
        "unreachable: PostgreSQL did not accept the connection (check ANTIFAZ_DATABASE_HOST, "
        "ANTIFAZ_DATABASE_PORT and that the server is running)"
    ),
    DatabaseProblem.TIMEOUT: (
        "timeout: PostgreSQL did not answer within ANTIFAZ_DATABASE_TIMEOUT_SECONDS"
    ),
    DatabaseProblem.AUTH_FAILED: (
        "auth_failed: PostgreSQL refused the user, the password or the database (check "
        "ANTIFAZ_DATABASE_USER, ANTIFAZ_DATABASE_PASSWORD and ANTIFAZ_DATABASE_NAME)"
    ),
    DatabaseProblem.TLS_FAILED: (
        "tls_failed: no verified TLS connection (ANTIFAZ_DATABASE_SSLMODE=verify-full needs TLS "
        "on the server and a certificate for that host name that this system trusts)"
    ),
    DatabaseProblem.TOO_OLD: "too_old: Antifaz needs PostgreSQL 16 or newer",
    DatabaseProblem.NOT_UTF8: "not_utf8: the database must use the UTF8 encoding",
    DatabaseProblem.NOT_MIGRATED: (
        "not_migrated: the database has no Antifaz tables yet: run `antifaz db migrate`"
    ),
    DatabaseProblem.BEHIND: (
        "behind: the database schema is older than this Antifaz: run `antifaz db migrate`"
    ),
    DatabaseProblem.AHEAD: (
        "ahead: the database schema is newer than this Antifaz: upgrade Antifaz (a schema is "
        "never migrated down)"
    ),
    DatabaseProblem.MIGRATION_CHANGED: (
        "migration_changed: an applied migration does not match this Antifaz (a changed file "
        "or a missing version); nothing was changed: restore the database from a backup"
    ),
    DatabaseProblem.OTHER: (
        "other: PostgreSQL refused the request (details are not shown because they can hold "
        "names and values; see the server's own log)"
    ),
}


class DatabaseError(Exception):
    """A database failure with a closed reason. Its text is fixed: never asyncpg's message."""

    def __init__(self, problem: DatabaseProblem) -> None:
        super().__init__(PROBLEM_TEXTS[problem])
        self.problem = problem


def _forget(error: BaseException) -> None:
    """Clear the locals of every finished frame of the error and of what it chains."""
    seen: set[int] = set()
    pending: list[BaseException | None] = [error]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        traceback.clear_frames(current.__traceback__)
        pending += [current.__cause__, current.__context__]


def _problem_of(error: Exception, tls: bool) -> DatabaseProblem:
    """The closed reason of an asyncpg, ssl or socket error (never its message)."""
    if isinstance(
        error,
        TimeoutError
        | asyncpg.QueryCanceledError
        | asyncpg.LockNotAvailableError
        | asyncpg.IdleInTransactionSessionTimeoutError,
    ):
        return DatabaseProblem.TIMEOUT
    if isinstance(
        error,
        asyncpg.InvalidPasswordError
        | asyncpg.InvalidAuthorizationSpecificationError
        | asyncpg.InvalidCatalogNameError,
    ):
        return DatabaseProblem.AUTH_FAILED
    if isinstance(error, ssl.SSLError):
        return DatabaseProblem.TLS_FAILED
    # asyncpg raises a bare ConnectionError when the server answers "no TLS" to verify-full.
    if tls and type(error) is ConnectionError:
        return DatabaseProblem.TLS_FAILED
    if isinstance(error, OSError):
        return DatabaseProblem.UNREACHABLE
    return DatabaseProblem.OTHER


def _raise(problem: DatabaseProblem) -> NoReturn:
    raise DatabaseError(problem)


async def _guarded[T](pending: Awaitable[T], tls: bool = False) -> T:
    """Await `pending`; any failure becomes a DatabaseError raised outside the except block."""
    problem: DatabaseProblem
    try:
        return await pending
    except BaseException as error:
        _forget(error)
        if not isinstance(error, Exception):  # cancellation, KeyboardInterrupt: not ours
            raise
        problem = _problem_of(error, tls)
    _raise(problem)


def tls_context(
    sslmode: Literal["disable", "verify-full"] | str,
) -> ssl.SSLContext | Literal[False]:
    """`verify-full`: certificate and host name checked against the system's CAs, TLS >= 1.2.
    `disable`: False (no TLS). Anything else is refused."""
    if sslmode == "disable":
        return False
    if sslmode != "verify-full":
        raise ValueError("ANTIFAZ_DATABASE_SSLMODE must be disable or verify-full")
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def session_settings(timeout: float) -> dict[str, str]:
    """What every session sets at startup (in milliseconds for the timeouts)."""
    milliseconds = str(max(1, round(timeout * 1000)))
    return {
        "application_name": APPLICATION_NAME,
        "statement_timeout": milliseconds,
        "lock_timeout": milliseconds,
        "idle_in_transaction_session_timeout": milliseconds,
        "log_parameter_max_length_on_error": "0",
    }


class Session:
    """One PostgreSQL connection. Every call passes the timeout; every failure is closed."""

    __slots__ = ("_connection", "_timeout")

    def __init__(self, connection: Any, timeout: float) -> None:
        self._connection = connection
        self._timeout = timeout

    async def execute(self, query: Sql, *args: object) -> None:
        await _guarded(self._connection.execute(query, *args, timeout=self._timeout))

    async def fetchval(self, query: Sql, *args: object) -> object:
        value: object = await _guarded(
            self._connection.fetchval(query, *args, timeout=self._timeout)
        )
        return value

    async def fetchrow(self, query: Sql, *args: object) -> dict[str, object] | None:
        row = await _guarded(self._connection.fetchrow(query, *args, timeout=self._timeout))
        return None if row is None else dict(row)

    async def fetch(self, query: Sql, *args: object) -> list[dict[str, object]]:
        rows = await _guarded(self._connection.fetch(query, *args, timeout=self._timeout))
        return [dict(row) for row in rows]

    async def close(self) -> None:
        """Close politely within the timeout; if that fails, drop the connection."""
        try:
            await _guarded(self._connection.close(timeout=self._timeout))
        except DatabaseError:
            self._connection.terminate()

    def __repr__(self) -> str:
        return f"Session(timeout={self._timeout:g})"

    __str__ = __repr__


async def _open(settings: Settings) -> Any:
    """asyncpg.connect with every parameter explicit (the password only as an argument)."""
    timeout = settings.database_timeout_seconds
    return await asyncpg.connect(
        host=settings.database_host,
        port=settings.database_port,
        user=settings.database_user,
        password=(
            settings.database_password.get_secret_value() if settings.database_password else ""
        ),
        database=settings.database_name,
        ssl=tls_context(settings.database_sslmode),
        direct_tls=False,
        target_session_attrs="any",
        krbsrvname=_KERBEROS_SERVICE,
        gsslib=_GSS_LIBRARY,
        server_settings=session_settings(timeout),
        command_timeout=timeout,
        timeout=timeout,
    )


async def connect(settings: Settings) -> Session:
    """A Session, or DatabaseError with a closed reason. Needs ANTIFAZ_DATABASE_HOST.

    The settings are checked here too (config.check_database_settings), before asyncpg is ever
    called: a caller that skips the check still cannot reach, say, a Unix socket path (no TLS
    there, whatever the mode says) or use an empty or example password. UnsafeConfigError names
    the variable, never the value."""
    if settings.database_host is None:
        raise ValueError("ANTIFAZ_DATABASE_HOST is not set: PostgreSQL is off")
    check_database_settings(settings)
    tls = settings.database_sslmode != "disable"
    connection = await _guarded(_open(settings), tls)
    return Session(connection, settings.database_timeout_seconds)


__all__ = [
    "APPLICATION_NAME",
    "PROBLEM_TEXTS",
    "DatabaseError",
    "DatabaseProblem",
    "Session",
    "Sql",
    "connect",
    "session_settings",
    "tls_context",
]
