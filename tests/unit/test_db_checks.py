"""check_database() on a fake session (issue 121, PR 8b-1, ADR-0021).

The real database runs these in tests/postgres (only with --postgres). Here a fake session
answers the same questions, so every closed reason is covered without PostgreSQL, also the ones
a PostgreSQL 16 cannot give (too_old) and a "behind" schema, which needs two migrations.
"""

import hashlib

import pytest

from antifaz.db import DatabaseError, DatabaseProblem, check_database
from antifaz.db import checks as checks_module
from antifaz.db.migrate import MIGRATIONS, Migration, Sql


def migration(version: int) -> Migration:
    sql = Sql(f"SELECT {version};\n")
    return Migration(
        version=version,
        name=f"{version:04d}_test",
        sql=sql,
        checksum=hashlib.sha256(sql.encode()).digest(),
    )


TWO = (migration(1), migration(2))


class FakeSession:
    def __init__(
        self,
        *,
        version: int = 160004,
        encoding: str = "UTF8",
        table: bool = True,
        applied: list[tuple[int, bytes]] | None = None,
        broken: DatabaseProblem | None = None,
        honour_limit: bool = True,
    ) -> None:
        self.answers: dict[str, object] = {
            checks_module.SERVER_VERSION: version,
            checks_module.SERVER_ENCODING: encoding,
            checks_module.SCHEMA_TABLE_EXISTS: table,
        }
        self.applied = applied if applied is not None else []
        self.broken = broken
        self.honour_limit = honour_limit
        self.queries: list[str] = []
        self.fetch_args: list[tuple[object, ...]] = []

    async def fetchval(self, query: str, *args: object) -> object:
        self.queries.append(query)
        if self.broken is not None:
            raise DatabaseError(self.broken)
        return self.answers[query]

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.queries.append(query)
        assert query == checks_module.APPLIED_MIGRATIONS
        self.fetch_args.append(args)
        rows = [{"version": v, "checksum": c} for v, c in sorted(self.applied)]
        if self.honour_limit and args:
            limit = args[0]
            assert type(limit) is int
            rows = rows[:limit]
        return rows


def applied(*migrations: Migration) -> list[tuple[int, bytes]]:
    return [(m.version, m.checksum) for m in migrations]


async def test_a_migrated_database_is_ready() -> None:
    result = await check_database(FakeSession(applied=applied(*TWO)), TWO)  # type: ignore[arg-type]

    assert result.ready
    assert result.problem is None
    assert result.version == 2


async def test_the_real_migrations_are_the_default() -> None:
    session = FakeSession(applied=applied(*MIGRATIONS))

    assert (await check_database(session)).ready  # type: ignore[arg-type]


@pytest.mark.parametrize(("version", "ready"), [(150012, False), (159999, False), (160000, True)])
async def test_postgresql_older_than_16_is_too_old(version: int, ready: bool) -> None:
    session = FakeSession(version=version, applied=applied(*TWO))

    result = await check_database(session, TWO)  # type: ignore[arg-type]

    assert result.ready is ready
    assert result.problem is (None if ready else DatabaseProblem.TOO_OLD)


@pytest.mark.parametrize("encoding", ["SQL_ASCII", "LATIN1", "WIN1252", "utf8"])
async def test_a_database_that_is_not_utf8_is_refused(encoding: str) -> None:
    session = FakeSession(encoding=encoding, applied=applied(*TWO))

    result = await check_database(session, TWO)  # type: ignore[arg-type]

    assert result.problem is DatabaseProblem.NOT_UTF8


async def test_without_the_version_table_it_is_not_migrated() -> None:
    session = FakeSession(table=False)

    result = await check_database(session, TWO)  # type: ignore[arg-type]

    assert result.problem is DatabaseProblem.NOT_MIGRATED
    assert result.version is None


async def test_an_empty_version_table_is_not_migrated() -> None:
    result = await check_database(FakeSession(applied=[]), TWO)  # type: ignore[arg-type]

    assert result.problem is DatabaseProblem.NOT_MIGRATED


async def test_an_older_schema_is_behind() -> None:
    result = await check_database(FakeSession(applied=applied(TWO[0])), TWO)  # type: ignore[arg-type]

    assert result.problem is DatabaseProblem.BEHIND
    assert result.version == 1


async def test_a_newer_schema_is_ahead() -> None:
    session = FakeSession(applied=applied(*TWO, migration(3)))

    result = await check_database(session, TWO)  # type: ignore[arg-type]

    assert result.problem is DatabaseProblem.AHEAD
    assert result.version == 3


async def test_a_changed_file_is_migration_changed() -> None:
    session = FakeSession(applied=[(1, TWO[0].checksum), (2, bytes(32))])

    result = await check_database(session, TWO)  # type: ignore[arg-type]

    assert result.problem is DatabaseProblem.MIGRATION_CHANGED


async def test_a_gap_in_the_applied_versions_is_migration_changed() -> None:
    result = await check_database(FakeSession(applied=applied(TWO[1])), TWO)  # type: ignore[arg-type]

    assert result.problem is DatabaseProblem.MIGRATION_CHANGED


@pytest.mark.parametrize("problem", [DatabaseProblem.TIMEOUT, DatabaseProblem.OTHER])
async def test_a_failing_query_gives_its_closed_reason(problem: DatabaseProblem) -> None:
    result = await check_database(FakeSession(broken=problem), TWO)  # type: ignore[arg-type]

    assert result.problem is problem
    assert not result.ready


async def test_the_applied_versions_are_read_with_a_limit() -> None:
    """Never the whole table: at most one row more than the known files (that row alone already
    makes the schema `ahead`)."""
    session = FakeSession(applied=applied(*TWO))

    await check_database(session, TWO)  # type: ignore[arg-type]

    assert "LIMIT $1" in checks_module.APPLIED_MIGRATIONS
    assert session.fetch_args == [(len(TWO) + 1,)]


async def test_more_rows_than_the_limit_is_a_closed_error() -> None:
    """A session that returns more rows than asked for is refused, not trusted."""
    rows = applied(*TWO, migration(3), migration(4))
    session = FakeSession(applied=rows, honour_limit=False)

    result = await check_database(session, TWO)  # type: ignore[arg-type]

    assert result.problem is DatabaseProblem.OTHER


async def test_many_newer_versions_are_ahead_within_the_limit() -> None:
    rows = applied(*TWO, *(migration(version) for version in range(3, 50)))
    session = FakeSession(applied=rows)

    result = await check_database(session, TWO)  # type: ignore[arg-type]

    assert result.problem is DatabaseProblem.AHEAD
    assert result.version == 3
