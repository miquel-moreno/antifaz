"""`antifaz db migrate`: bring the schema up to SCHEMA_VERSION (PR 8b-1, ADR-0021).

Own runner, no Alembic. Forward only:
1. The server must be PostgreSQL >= 16 with a UTF8 database (too_old, not_utf8): checked before
   anything is created.
2. pg_advisory_lock(LOCK_ID) is held for the whole run, so two `migrate` at once apply each
   file once (the second waits, then finds nothing to do). lock_timeout bounds the wait.
3. antifaz_schema_version is created if missing. Its rows are compared with the files: a newer
   schema (ahead) or a changed or missing one (migration_changed) is refused and nothing runs.
4. Each pending file runs in its own transaction together with its row (version, SHA-256); a
   failure rolls that file back and stops.

`serve` never migrates: an admin runs `antifaz db migrate` on purpose (decision D7).
"""

import contextlib
from collections.abc import Sequence
from dataclasses import dataclass

from antifaz.db.checks import applied_migrations, server_problem
from antifaz.db.connect import DatabaseError, DatabaseProblem, Session, Sql
from antifaz.db.schema import MIGRATIONS, SCHEMA_VERSION, Migration, compare, load_migrations

# A fixed bigint for pg_advisory_lock: "antifaz" in ASCII and a 1.
LOCK_ID = 0x616E_7469_6661_7A01
LOCK = Sql("SELECT pg_advisory_lock($1)")
UNLOCK = Sql("SELECT pg_advisory_unlock($1)")
CREATE_VERSION_TABLE = Sql(
    "CREATE TABLE IF NOT EXISTS antifaz_schema_version ("
    " version integer PRIMARY KEY,"
    " checksum bytea NOT NULL,"
    " applied_at timestamptz NOT NULL DEFAULT now())"
)
RECORD_VERSION = Sql("INSERT INTO antifaz_schema_version (version, checksum) VALUES ($1, $2)")
BEGIN = Sql("BEGIN")
COMMIT = Sql("COMMIT")
ROLLBACK = Sql("ROLLBACK")
# What `migrate` refuses to build on: an applied schema that is not a prefix of the files.
_REFUSED = frozenset({DatabaseProblem.AHEAD, DatabaseProblem.MIGRATION_CHANGED})


@dataclass(frozen=True)
class MigrationResult:
    applied: tuple[int, ...]  # the versions this run applied (empty: nothing to do)
    version: int  # the schema version now


async def _apply(session: Session, migration: Migration) -> None:
    await session.execute(BEGIN)
    try:
        await session.execute(migration.sql)
        await session.execute(RECORD_VERSION, migration.version, migration.checksum)
        await session.execute(COMMIT)
    except BaseException:
        with contextlib.suppress(DatabaseError):
            await session.execute(ROLLBACK)
        raise


async def _migrate_locked(session: Session, known: Sequence[Migration]) -> MigrationResult:
    await session.execute(CREATE_VERSION_TABLE)
    problem, version = compare(await applied_migrations(session, len(known)), known)
    if problem is not None and problem in _REFUSED:
        raise DatabaseError(problem)
    done = []
    for migration in known[version or 0 :]:
        await _apply(session, migration)
        done.append(migration.version)
    return MigrationResult(applied=tuple(done), version=len(known))


async def migrate(
    session: Session, migrations: Sequence[Migration] | None = None
) -> MigrationResult:
    """Apply every pending migration; DatabaseError with a closed reason otherwise."""
    known = MIGRATIONS if migrations is None else tuple(migrations)
    problem = await server_problem(session)
    if problem is not None:
        raise DatabaseError(problem)
    await session.fetchval(LOCK, LOCK_ID)
    try:
        return await _migrate_locked(session, known)
    finally:
        with contextlib.suppress(DatabaseError):
            await session.fetchval(UNLOCK, LOCK_ID)


__all__ = [
    "LOCK_ID",
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "Migration",
    "MigrationResult",
    "Sql",
    "load_migrations",
    "migrate",
]
