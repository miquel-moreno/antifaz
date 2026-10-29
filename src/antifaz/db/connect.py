"""Stub (red): the PostgreSQL adapter of PR 8b-1."""

from enum import StrEnum
from typing import NewType

from antifaz.config import Settings

Sql = NewType("Sql", str)


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


PROBLEM_TEXTS: dict[DatabaseProblem, str] = {}


class DatabaseError(Exception):
    def __init__(self, problem: DatabaseProblem) -> None:
        super().__init__(problem.value)
        self.problem = problem


class Session:
    async def execute(self, query: Sql, *args: object) -> None:
        raise NotImplementedError

    async def fetchval(self, query: Sql, *args: object) -> object:
        raise NotImplementedError

    async def fetch(self, query: Sql, *args: object) -> list[dict[str, object]]:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


def tls_context(sslmode: str) -> object:
    raise NotImplementedError


async def connect(settings: Settings) -> Session:
    raise NotImplementedError
