"""The migration files (issue 121, PR 8b-1, ADR-0021), without PostgreSQL.

Own runner, no Alembic: `src/antifaz/db/migrations/NNNN_name.sql`, numbered from 0001 without
gaps, each applied once with its SHA-256. SCHEMA_VERSION is the last file's number. A released
file never changes: its checksum is pinned here, so an edit fails this test before it can fail
`antifaz db migrate` on a real database (migration_changed).
"""

import hashlib
import re
from importlib import resources
from pathlib import Path

import pytest

from antifaz.db.migrate import MIGRATIONS, SCHEMA_VERSION, Migration, load_migrations
from antifaz.keys.records import HASH_BYTES, MAX_NAME_LENGTH, Scope

FOLDER = Path(str(resources.files("antifaz.db").joinpath("migrations")))
FILE_NAME = re.compile(r"(\d{4})_[a-z0-9_]+\.sql")
# Pinned when each file was released. Never update a line: add a new file instead.
RELEASED_CHECKSUMS = {
    1: "23456710d1ea57b043928b96562ddcd0e5f8bc5f5c9b35107612ba69f214a056",
}


def sql_files() -> list[Path]:
    return sorted(path for path in FOLDER.iterdir() if path.suffix == ".sql")


def test_there_is_at_least_one_migration() -> None:
    assert sql_files()
    assert MIGRATIONS


def test_every_file_in_the_folder_is_a_numbered_sql_file() -> None:
    names = sorted(path.name for path in FOLDER.iterdir() if path.name != "__pycache__")

    assert names == [path.name for path in sql_files()]
    assert all(FILE_NAME.fullmatch(name) for name in names), names


def test_the_numbers_start_at_one_and_have_no_gaps() -> None:
    numbers = [int(FILE_NAME.fullmatch(path.name).group(1)) for path in sql_files()]  # type: ignore[union-attr]

    assert numbers == list(range(1, len(numbers) + 1))


def test_the_schema_version_is_the_last_file() -> None:
    assert len(sql_files()) == SCHEMA_VERSION
    assert MIGRATIONS[-1].version == SCHEMA_VERSION
    assert [migration.version for migration in MIGRATIONS] == list(range(1, SCHEMA_VERSION + 1))


def test_each_migration_carries_the_sha256_of_its_file() -> None:
    for migration, path in zip(MIGRATIONS, sql_files(), strict=True):
        assert migration.checksum == hashlib.sha256(path.read_bytes()).digest()
        assert migration.sql == path.read_text(encoding="utf-8")
        assert migration.name == path.stem


def test_released_migrations_never_change() -> None:
    for migration in MIGRATIONS:
        if migration.version in RELEASED_CHECKSUMS:
            assert migration.checksum.hex() == RELEASED_CHECKSUMS[migration.version]
    assert set(RELEASED_CHECKSUMS) <= {migration.version for migration in MIGRATIONS}


def test_the_files_are_utf8_with_lf_line_ends() -> None:
    """A CRLF checkout would change the checksum (.gitattributes keeps them LF)."""
    for path in sql_files():
        data = path.read_bytes()
        data.decode("utf-8")
        assert b"\r" not in data, path.name
        assert not data.startswith(b"\xef\xbb\xbf"), path.name


@pytest.mark.parametrize("statement", ["BEGIN", "COMMIT", "ROLLBACK", "START TRANSACTION"])
def test_no_file_controls_its_own_transaction(statement: str) -> None:
    """The runner wraps each file and its version row in one transaction."""
    for path in sql_files():
        code = "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.startswith("--")
        )
        # plpgsql bodies have BEGIN ... END; a transaction statement ends with ";" at once.
        assert not re.search(rf"(?im)^\s*{statement}\s*;", code), (path.name, statement)


def test_the_scopes_in_sql_are_the_python_scopes() -> None:
    sql = MIGRATIONS[0].sql
    listed = re.search(r"scopes <@ ARRAY\[([^\]]*)\]", sql)

    assert listed is not None
    assert set(re.findall(r"'([a-z_]+)'", listed.group(1))) == {scope.value for scope in Scope}
    assert f"BETWEEN 1 AND {len(Scope)}" in sql


def test_the_name_length_and_hash_size_in_sql_are_the_python_ones() -> None:
    sql = MIGRATIONS[0].sql

    assert f"char_length(name) BETWEEN 1 AND {MAX_NAME_LENGTH}" in sql
    assert f"octet_length(key_hash) = {HASH_BYTES}" in sql


def _folder(tmp_path: Path, *names: str) -> Path:
    for name in names:
        (tmp_path / name).write_text("SELECT 1;\n", encoding="utf-8", newline="\n")
    return tmp_path


def test_loading_reads_every_file_in_order(tmp_path: Path) -> None:
    loaded = load_migrations(_folder(tmp_path, "0002_b.sql", "0001_a.sql"))

    assert [(m.version, m.name) for m in loaded] == [(1, "0001_a"), (2, "0002_b")]
    assert all(type(m) is Migration for m in loaded)


@pytest.mark.parametrize(
    "names",
    [
        ("0001_a.sql", "0003_c.sql"),  # a gap
        ("0002_b.sql",),  # does not start at 1
        ("0001_a.sql", "0001_b.sql"),  # the same number twice
        ("0001_a.sql", "1_b.sql"),  # a badly named file
        ("0001_a.sql", "0002_B.sql"),
        ("0001_a.sql", "0002_b.txt"),  # anything else in the folder
        (),  # nothing at all
    ],
)
def test_loading_refuses_a_folder_that_is_not_a_clean_sequence(
    tmp_path: Path, names: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match="migration"):
        load_migrations(_folder(tmp_path, *names))


def test_loading_refuses_a_file_with_crlf(tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_bytes(b"SELECT 1;\r\n")

    with pytest.raises(ValueError, match="migration"):
        load_migrations(tmp_path)
