"""Is the database ready for this Antifaz? And how many keys does it hold? (PR 8b-1, ADR-0021)

check_database(session) answers ready, or one closed reason, in this order: the query failed
(its own reason: timeout, unreachable, other...), too_old (PostgreSQL < 16), not_utf8,
not_migrated (no version table, or an empty one), ahead, migration_changed, behind. It only
reads: an unmigrated database stays untouched.

key_counts(session, now) counts active, revoked and expired keys with SQL only (`now` is given,
so the CLI uses the clock and tests use a fixed time). Rows that do not pass the Python checks
are counted in 8b-2.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from antifaz.db.connect import DatabaseError, DatabaseProblem, Session, Sql
from antifaz.db.schema import MIGRATIONS, Migration, compare

MIN_SERVER_VERSION = 160000  # PostgreSQL 16 (server_version_num)
SERVER_VERSION = Sql("SELECT current_setting('server_version_num')::integer")
SERVER_ENCODING = Sql("SELECT current_setting('server_encoding')")
SCHEMA_TABLE_EXISTS = Sql("SELECT to_regclass('antifaz_schema_version') IS NOT NULL")
APPLIED_MIGRATIONS = Sql("SELECT version, checksum FROM antifaz_schema_version ORDER BY version")
COUNT_KEYS = Sql(
    "SELECT"
    " count(*) FILTER (WHERE revoked_at IS NULL AND (expires_at IS NULL OR expires_at > $1))"
    " AS active,"
    " count(*) FILTER (WHERE revoked_at IS NOT NULL) AS revoked,"
    " count(*) FILTER (WHERE revoked_at IS NULL AND expires_at <= $1) AS expired"
    " FROM virtual_keys"
)


@dataclass(frozen=True)
class DatabaseCheck:
    problem: DatabaseProblem | None
    version: int | None

    @property
    def ready(self) -> bool:
        return self.problem is None


@dataclass(frozen=True)
class KeyCounts:
    active: int
    revoked: int
    expired: int


async def server_problem(session: Session) -> DatabaseProblem | None:
    """too_old or not_utf8, or None. Raises DatabaseError if a query fails."""
    version = await session.fetchval(SERVER_VERSION)
    if type(version) is not int or version < MIN_SERVER_VERSION:
        return DatabaseProblem.TOO_OLD
    if await session.fetchval(SERVER_ENCODING) != "UTF8":
        return DatabaseProblem.NOT_UTF8
    return None


async def applied_migrations(session: Session) -> list[tuple[int, bytes]]:
    rows = await session.fetch(APPLIED_MIGRATIONS)
    applied = []
    for row in rows:
        version, checksum = row["version"], row["checksum"]
        if type(version) is not int or not isinstance(checksum, bytes):
            raise DatabaseError(DatabaseProblem.MIGRATION_CHANGED)
        applied.append((version, checksum))
    return applied


async def _check(session: Session, known: Sequence[Migration]) -> DatabaseCheck:
    problem = await server_problem(session)
    if problem is not None:
        return DatabaseCheck(problem, None)
    if await session.fetchval(SCHEMA_TABLE_EXISTS) is not True:
        return DatabaseCheck(DatabaseProblem.NOT_MIGRATED, None)
    state, version = compare(await applied_migrations(session), known)
    return DatabaseCheck(state, version)


async def check_database(
    session: Session, migrations: Sequence[Migration] | None = None
) -> DatabaseCheck:
    """Ready, or the closed reason why not. Never raises a DatabaseError: a failing query is
    reported with its own reason."""
    known = MIGRATIONS if migrations is None else tuple(migrations)
    try:
        return await _check(session, known)
    except DatabaseError as error:
        problem = error.problem
    return DatabaseCheck(problem, None)


async def key_counts(session: Session, now: datetime) -> KeyCounts:
    """Active (not revoked, not expired at `now`), revoked, and expired (not revoked)."""
    row = await session.fetchrow(COUNT_KEYS, now)
    if row is None:  # pragma: no cover - an aggregate always returns one row
        return KeyCounts(0, 0, 0)
    active, revoked, expired = row["active"], row["revoked"], row["expired"]
    if type(active) is not int or type(revoked) is not int or type(expired) is not int:
        raise DatabaseError(DatabaseProblem.OTHER)
    return KeyCounts(active=active, revoked=revoked, expired=expired)


__all__ = [
    "MIN_SERVER_VERSION",
    "DatabaseCheck",
    "KeyCounts",
    "check_database",
    "key_counts",
    "server_problem",
]
