"""The migration files and how an applied schema compares with them (PR 8b-1, ADR-0021).

Standard library only (no asyncpg): the files are `antifaz/db/migrations/NNNN_name.sql`,
shipped in the wheel, numbered from 0001 without gaps, UTF-8 with LF line ends (a CRLF checkout
would change the checksum; .gitattributes keeps them LF). Each one is applied once, in its own
transaction, and recorded in antifaz_schema_version with its SHA-256. A released file never
changes (tests/unit/test_db_migrations.py pins the checksums): a change is a new file.

SCHEMA_VERSION is the number of the last file; a test ties them.
"""

import hashlib
import hmac
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from antifaz.db.connect import DatabaseProblem, Sql

SCHEMA_VERSION = 1
_FILE_NAME = re.compile(r"(\d{4})_[a-z0-9_]+\.sql")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: Sql = field(repr=False)
    checksum: bytes = field(repr=False)


def _folder() -> Traversable:
    return resources.files("antifaz.db").joinpath("migrations")


def load_migrations(folder: Traversable | Path | None = None) -> tuple[Migration, ...]:
    """Every migration file of `folder` (default: the package's), in order. ValueError if the
    folder is not exactly 0001..N, each a UTF-8 file with LF line ends."""
    entries = [entry for entry in (folder or _folder()).iterdir() if entry.name != "__pycache__"]
    numbered: dict[int, Traversable | Path] = {}
    for entry in entries:
        match = _FILE_NAME.fullmatch(entry.name)
        if match is None or not entry.is_file():
            raise ValueError("migration files: only NNNN_name.sql files belong in the folder")
        number = int(match.group(1))
        if number in numbered:
            raise ValueError("migration files: two files with the same number")
        numbered[number] = entry
    if sorted(numbered) != list(range(1, len(numbered) + 1)) or not numbered:
        raise ValueError("migration files: the numbers must start at 0001 and have no gaps")
    migrations = []
    for number in sorted(numbered):
        entry = numbered[number]
        data = entry.read_bytes()
        if b"\r" in data or data.startswith(b"\xef\xbb\xbf"):
            raise ValueError("migration files: UTF-8 without BOM and with LF line ends only")
        migrations.append(
            Migration(
                version=number,
                name=entry.name.removesuffix(".sql"),
                sql=Sql(data.decode("utf-8")),
                checksum=hashlib.sha256(data).digest(),
            )
        )
    return tuple(migrations)


MIGRATIONS = load_migrations()


def compare(
    applied: Iterable[tuple[int, bytes]], known: Sequence[Migration]
) -> tuple[DatabaseProblem | None, int | None]:
    """(problem or None, version) of a schema whose version table holds `applied`.

    Empty: not_migrated. A version newer than every known file: ahead (never touched). A gap,
    or a checksum that differs from its file: migration_changed. Fewer than known: behind.
    """
    rows = sorted(applied)
    if not rows:
        return DatabaseProblem.NOT_MIGRATED, None
    versions = [version for version, _ in rows]
    top = versions[-1]
    if top > len(known):
        return DatabaseProblem.AHEAD, top
    if versions != list(range(1, top + 1)):
        return DatabaseProblem.MIGRATION_CHANGED, top
    for version, checksum in rows:
        if not hmac.compare_digest(bytes(checksum), known[version - 1].checksum):
            return DatabaseProblem.MIGRATION_CHANGED, top
    if top < len(known):
        return DatabaseProblem.BEHIND, top
    return None, top


__all__ = ["MIGRATIONS", "SCHEMA_VERSION", "Migration", "compare", "load_migrations"]
