"""Stub (red): the migration runner of PR 8b-1."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from antifaz.db.connect import Session, Sql

SCHEMA_VERSION = 0


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: Sql
    checksum: bytes


@dataclass(frozen=True)
class MigrationResult:
    applied: tuple[int, ...]
    version: int


@dataclass(frozen=True)
class KeyCounts:
    active: int
    revoked: int
    expired: int


def load_migrations(folder: Path | None = None) -> tuple[Migration, ...]:
    raise NotImplementedError


MIGRATIONS: tuple[Migration, ...] = ()


async def migrate(session: Session) -> MigrationResult:
    raise NotImplementedError


async def key_counts(session: Session, now: datetime) -> KeyCounts:
    raise NotImplementedError


__all__ = ["MIGRATIONS", "SCHEMA_VERSION", "Migration", "Sql", "load_migrations"]
