"""Stub (red): check_database() of PR 8b-1."""

from collections.abc import Sequence
from dataclasses import dataclass

from antifaz.db.connect import DatabaseProblem, Session, Sql
from antifaz.db.migrate import Migration

SERVER_VERSION = Sql("")
SERVER_ENCODING = Sql(" ")
SCHEMA_TABLE_EXISTS = Sql("  ")
APPLIED_MIGRATIONS = Sql("   ")


@dataclass(frozen=True)
class DatabaseCheck:
    problem: DatabaseProblem | None
    version: int | None

    @property
    def ready(self) -> bool:
        return self.problem is None


async def check_database(
    session: Session, migrations: Sequence[Migration] | None = None
) -> DatabaseCheck:
    raise NotImplementedError
