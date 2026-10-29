"""PostgreSQL for the virtual keys (issue 121, PR 8b-1, ADR-0021).

Optional: without ANTIFAZ_DATABASE_HOST, Antifaz works as in v0.1. Only `antifaz db` (and, from
8c, `antifaz keys`) imports this package, and only inside its run(): the keys library, the
settings, the gateway, the panel and the rest of the CLI never load it or asyncpg
(tests/unit/test_db_imports.py).

- connect.py: connect(settings) -> Session, the only module that imports asyncpg. Every call
  has a timeout, every failure is a DatabaseError with a closed reason (DatabaseProblem) and a
  fixed text that never holds asyncpg's message, a user, a host or a value.
- schema.py: the migration files (migrations/NNNN_name.sql, shipped in the wheel) with their
  SHA-256, SCHEMA_VERSION, and how an applied schema compares with them.
- checks.py: check_database(session) -> ready or a closed reason; key_counts(session, now).
- migrate.py: migrate(session), forward only, under an advisory lock, one transaction per file.

The virtual keys table (migration 0001) refuses deletes and truncates, and changes to a key's
number, hash, pepper version, creation time or revocation. The PostgreSQL key store comes in
8b-2.

Forbidden: a DSN or the PG* variables; logging or printing asyncpg's errors; queries built with
f-strings, % or .format(); migrating from `serve`.
"""

from antifaz.db.checks import DatabaseCheck, KeyCounts, check_database, key_counts
from antifaz.db.connect import (
    PROBLEM_TEXTS,
    DatabaseError,
    DatabaseProblem,
    Session,
    Sql,
    connect,
)
from antifaz.db.migrate import MigrationResult, migrate
from antifaz.db.schema import MIGRATIONS, SCHEMA_VERSION, Migration

__all__ = [
    "MIGRATIONS",
    "PROBLEM_TEXTS",
    "SCHEMA_VERSION",
    "DatabaseCheck",
    "DatabaseError",
    "DatabaseProblem",
    "KeyCounts",
    "Migration",
    "MigrationResult",
    "Session",
    "Sql",
    "check_database",
    "connect",
    "key_counts",
    "migrate",
]
