"""Migrations, checks and the virtual keys table on a real PostgreSQL 16 (issue 121, PR 8b-1).

Only with --postgres (`make test-postgres` and CI). Each test has its own empty schema
(tests/postgres/conftest.py). Direct SQL plays the part of anyone with the app role: every
CHECK refuses its bad row, rows cannot be deleted or truncated, and what identifies a key or a
revocation cannot be changed. Times are given, never read from a clock (no sleeps).
"""

import asyncio
import os
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from antifaz.cli import main
from antifaz.db import (
    SCHEMA_VERSION,
    DatabaseError,
    DatabaseProblem,
    check_database,
    connect,
    key_counts,
    migrate,
)
from antifaz.db.connect import Session, Sql
from antifaz.keys.records import check_name
from tests.postgres.conftest import Schema

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 10, 29, 12, 0, 0, 123456, tzinfo=UTC)
INSERT = """
    INSERT INTO virtual_keys
        (name, scopes, created_at, expires_at, revoked_at, key_hash, pepper_version,
         per_minute_limit, per_day_limit)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
    RETURNING id
"""
CREATED_AT = Sql("SELECT created_at FROM virtual_keys WHERE id = $1")
SET_MADRID = Sql("SET TimeZone = 'Europe/Madrid'")
VERSIONS = Sql("SELECT version FROM antifaz_schema_version ORDER BY version")


def good_row(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": "facturas",
        "scopes": ["openai_chat", "scan"],
        "created_at": NOW,
        "expires_at": NOW + timedelta(days=90),
        "revoked_at": None,
        "key_hash": os.urandom(32),
        "pepper_version": 1,
        "per_minute_limit": 60,
        "per_day_limit": 1000,
    }
    row.update(changes)
    return row


async def insert(connection: Any, **changes: Any) -> int:
    row = good_row(**changes)
    key_id: int = await connection.fetchval(INSERT, *row.values())
    return key_id


async def migrated(schema: Schema) -> Session:
    """A session on a migrated schema (the caller closes it)."""
    session = await connect(schema.settings())
    await migrate(session)
    return session


async def prepare(schema: Schema) -> None:
    """Migrate the test's schema and close the session."""
    session = await migrated(schema)
    await session.close()


# --- Migrations -------------------------------------------------------------------------------


async def test_an_empty_database_is_migrated_to_the_last_version(schema: Schema) -> None:
    session = await connect(schema.settings())
    try:
        before = await check_database(session)
        result = await migrate(session)
        after = await check_database(session)
        versions = [row["version"] for row in await session.fetch(VERSIONS)]
    finally:
        await session.close()

    assert before.problem is DatabaseProblem.NOT_MIGRATED
    assert result.applied == tuple(range(1, SCHEMA_VERSION + 1))
    assert result.version == SCHEMA_VERSION == 1
    assert after.ready
    assert after.version == SCHEMA_VERSION
    assert versions == list(range(1, SCHEMA_VERSION + 1))


async def test_a_second_migrate_changes_nothing(schema: Schema) -> None:
    session = await migrated(schema)
    try:
        again = await migrate(session)
    finally:
        await session.close()

    assert again.applied == ()
    assert again.version == SCHEMA_VERSION


async def test_two_concurrent_migrates_apply_each_file_once(schema: Schema) -> None:
    first = await connect(schema.settings())
    second = await connect(schema.settings())
    try:
        results = await asyncio.gather(migrate(first), migrate(second))
        versions = [row["version"] for row in await first.fetch(VERSIONS)]
    finally:
        await first.close()
        await second.close()

    applied = sorted(version for result in results for version in result.applied)
    assert applied == list(range(1, SCHEMA_VERSION + 1))
    assert versions == list(range(1, SCHEMA_VERSION + 1))


async def test_a_changed_migration_is_refused(schema: Schema) -> None:
    await prepare(schema)
    raw = await schema.raw()
    await raw.execute("UPDATE antifaz_schema_version SET checksum = $1", bytes(32))
    await raw.close()
    session = await connect(schema.settings())
    try:
        result = await check_database(session)
        with pytest.raises(DatabaseError) as caught:
            await migrate(session)
    finally:
        await session.close()

    assert result.problem is DatabaseProblem.MIGRATION_CHANGED
    assert caught.value.problem is DatabaseProblem.MIGRATION_CHANGED


async def test_a_newer_schema_is_ahead_and_never_migrated(schema: Schema) -> None:
    session = await migrated(schema)
    raw = await schema.raw()
    await raw.execute(
        "INSERT INTO antifaz_schema_version (version, checksum) VALUES ($1, $2)",
        SCHEMA_VERSION + 1,
        bytes(32),
    )
    await raw.close()
    try:
        result = await check_database(session)
        with pytest.raises(DatabaseError) as caught:
            await migrate(session)
    finally:
        await session.close()

    assert result.problem is DatabaseProblem.AHEAD
    assert caught.value.problem is DatabaseProblem.AHEAD


async def test_checking_an_unmigrated_database_creates_nothing(schema: Schema) -> None:
    session = await connect(schema.settings())
    try:
        result = await check_database(session)
    finally:
        await session.close()
    raw = await schema.raw()
    tables = await raw.fetchval(
        "SELECT count(*) FROM pg_tables WHERE schemaname = current_schema()"
    )
    await raw.close()

    assert result.problem is DatabaseProblem.NOT_MIGRATED
    assert tables == 0


async def test_a_sql_ascii_database_is_refused_before_anything_is_created(
    ascii_schema: Schema,
) -> None:
    session = await connect(ascii_schema.settings())
    try:
        result = await check_database(session)
        with pytest.raises(DatabaseError) as caught:
            await migrate(session)
    finally:
        await session.close()
    raw = await ascii_schema.raw()
    tables = await raw.fetchval(
        "SELECT count(*) FROM pg_tables WHERE schemaname = current_schema()"
    )
    await raw.close()

    assert result.problem is DatabaseProblem.NOT_UTF8
    assert caught.value.problem is DatabaseProblem.NOT_UTF8
    assert tables == 0


# --- The session ------------------------------------------------------------------------------


async def test_the_session_settings_are_in_force(schema: Schema) -> None:
    session = await connect(schema.settings())
    try:
        values = {
            name: await session.fetchval(Sql("SELECT current_setting($1)"), name)
            for name in (
                "application_name",
                "statement_timeout",
                "lock_timeout",
                "idle_in_transaction_session_timeout",
                "log_parameter_max_length_on_error",
            )
        }
        context = await session.fetchval(
            Sql("SELECT context FROM pg_settings WHERE name = $1"),
            "log_parameter_max_length_on_error",
        )
    finally:
        await session.close()

    assert values == {
        "application_name": "antifaz",
        "statement_timeout": "10s",
        "lock_timeout": "10s",
        "idle_in_transaction_session_timeout": "10s",
        "log_parameter_max_length_on_error": "0",
    }
    assert context == "user"  # a normal role may set it for its own session


@pytest.mark.parametrize("zone", [None, "Europe/Madrid"])
async def test_times_come_back_in_utc_with_their_microseconds(
    schema: Schema, zone: str | None
) -> None:
    session = await migrated(schema)
    raw = await schema.raw()
    key_id = await insert(raw)
    await raw.close()
    try:
        if zone is not None:
            await session.execute(SET_MADRID)
        created = await session.fetchval(CREATED_AT, key_id)
    finally:
        await session.close()

    assert isinstance(created, datetime)
    assert created.tzinfo is UTC
    assert created == NOW
    assert created.microsecond == NOW.microsecond


async def test_key_counts_use_the_given_time(schema: Schema) -> None:
    session = await migrated(schema)
    raw = await schema.raw()
    await insert(raw)  # active
    await insert(raw, expires_at=None)  # active, no expiry
    await insert(raw, revoked_at=NOW + timedelta(seconds=1))  # revoked
    await insert(raw, created_at=NOW - timedelta(days=2), expires_at=NOW)  # expired at NOW
    await insert(raw, revoked_at=NOW, created_at=NOW - timedelta(days=2), expires_at=NOW)
    await raw.close()
    try:
        counts = await key_counts(session, NOW)
        earlier = await key_counts(session, NOW - timedelta(days=1))
    finally:
        await session.close()

    assert (counts.active, counts.revoked, counts.expired) == (2, 2, 1)
    assert (earlier.active, earlier.revoked, earlier.expired) == (3, 2, 0)


# --- The table refuses bad rows ---------------------------------------------------------------

BAD_ROWS = {
    "empty name": {"name": ""},
    "name too long": {"name": "a" * 65},
    "control in name": {"name": "fac\x01turas"},
    "DEL in name": {"name": "fac\x7fturas"},
    "C1 control in name": {"name": "fac\x85turas"},
    "line separator": {"name": "fac\u2028turas"},
    "zero width space": {"name": "fac\u200bturas"},
    "bidi override": {"name": "fac\u202eturas"},
    "bidi isolate": {"name": "fac\u2066turas"},
    "byte order mark": {"name": "fac\ufeffturas"},
    "soft hyphen": {"name": "fac\u00adturas"},
    "braille blank": {"name": "fac\u2800turas"},
    "leading space": {"name": " facturas"},
    "trailing space": {"name": "facturas "},
    "leading no-break space": {"name": "\u00a0facturas"},
    "trailing ideographic space": {"name": "facturas\u3000"},
    "no scope": {"scopes": []},
    "too many scopes": {"scopes": ["openai_chat", "anthropic_messages", "models", "scan", "scan"]},
    "unknown scope": {"scopes": ["admin"]},
    "repeated scope": {"scopes": ["scan", "scan"]},
    "null scope": {"scopes": ["scan", None]},
    "expiry at creation": {"expires_at": NOW},
    "expiry before creation": {"expires_at": NOW - timedelta(seconds=1)},
    "revoked before creation": {"revoked_at": NOW - timedelta(microseconds=1)},
    "short hash": {"key_hash": bytes(31)},
    "long hash": {"key_hash": bytes(33)},
    "pepper version 0": {"pepper_version": 0},
    "per-minute limit 0": {"per_minute_limit": 0},
    "per-day limit 0": {"per_day_limit": 0},
}


@pytest.mark.parametrize("changes", BAD_ROWS.values(), ids=BAD_ROWS.keys())
async def test_every_check_refuses_its_bad_row(schema: Schema, changes: dict[str, Any]) -> None:
    await prepare(schema)
    raw = await schema.raw()
    try:
        await insert(raw)  # the good row goes in: the bad one fails for its own reason
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await insert(raw, **changes)
    finally:
        await raw.close()


@pytest.mark.parametrize("value", ["infinity", "-infinity"])
@pytest.mark.parametrize("column", ["created_at", "expires_at", "revoked_at"])
async def test_infinite_times_are_refused(schema: Schema, column: str, value: str) -> None:
    await prepare(schema)
    raw = await schema.raw()
    times = {"created_at": "now()", "expires_at": "NULL", "revoked_at": "NULL"}
    times[column] = "$1::text::timestamptz"  # the infinity values travel as text
    # Column names and SQL words fixed in this test; the time travels as $1.
    statement = (
        "INSERT INTO virtual_keys (name, scopes, key_hash, pepper_version, created_at, "  # noqa: S608
        f"expires_at, revoked_at) VALUES ('a', '{{scan}}', $2, 1, {times['created_at']}, "
        f"{times['expires_at']}, {times['revoked_at']})"
    )
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await raw.execute(statement, value, os.urandom(32))
    finally:
        await raw.close()


@pytest.mark.parametrize(
    "changes",
    [{"pepper_version": 32768}, {"per_minute_limit": 2**31}, {"per_day_limit": 2**31}],
    ids=["pepper version over smallint", "per-minute over integer", "per-day over integer"],
)
async def test_values_over_the_column_range_are_refused(
    schema: Schema, changes: dict[str, Any]
) -> None:
    await prepare(schema)
    raw = await schema.raw()
    try:
        with pytest.raises((asyncpg.NumericValueOutOfRangeError, OverflowError, asyncpg.DataError)):
            await insert(raw, **changes)
    finally:
        await raw.close()


async def test_the_same_hash_and_pepper_version_twice_is_refused(schema: Schema) -> None:
    await prepare(schema)
    raw = await schema.raw()
    digest = os.urandom(32)
    try:
        await insert(raw, key_hash=digest)
        await insert(raw, key_hash=digest, pepper_version=2)  # another version: fine
        with pytest.raises(asyncpg.UniqueViolationError):
            await insert(raw, key_hash=digest)
    finally:
        await raw.close()


async def test_an_explicit_id_is_refused(schema: Schema) -> None:
    await prepare(schema)
    raw = await schema.raw()
    try:
        with pytest.raises(asyncpg.GeneratedAlwaysError):
            await raw.execute(
                "INSERT INTO virtual_keys (id, name, scopes, created_at, key_hash, "
                "pepper_version) VALUES (42, 'a', '{scan}', now(), $1, 1)",
                os.urandom(32),
            )
    finally:
        await raw.close()


PYTHON_WHITESPACE = [chr(point) for point in range(0x110000) if chr(point).isspace()]


@pytest.mark.parametrize("space", PYTHON_WHITESPACE, ids=lambda c: f"U+{ord(c):04X}")
async def test_whitespace_python_strips_is_refused_at_either_end(
    schema: Schema, space: str
) -> None:
    await prepare(schema)
    raw = await schema.raw()
    try:
        for name in (f"{space}facturas", f"facturas{space}"):
            with pytest.raises(asyncpg.CheckViolationError):
                await insert(raw, name=name)
    finally:
        await raw.close()


@pytest.mark.parametrize(
    "character",
    ["\ue000", "\U000e0001", "\u180e", "\u3164"],
    ids=["private use", "language tag", "mongolian vowel separator", "hangul filler"],
)
async def test_the_documented_gap_python_refuses_what_sql_lets_in(
    schema: Schema, character: str
) -> None:
    """The full Unicode rules of a name stay in Python (ADR-0021): a store must check names
    with keys.records.check_name before writing them."""
    name = f"fac{character}turas"
    with pytest.raises(ValueError):
        check_name(name)
    await prepare(schema)
    raw = await schema.raw()
    try:
        await insert(raw, name=name)  # SQL lets it in
    finally:
        await raw.close()
    assert unicodedata.category(character) in {"Co", "Cf", "Lo"}


# --- Keys are never deleted and their identity never changes ----------------------------------


async def test_a_key_cannot_be_deleted(schema: Schema) -> None:
    await prepare(schema)
    raw = await schema.raw()
    try:
        key_id = await insert(raw)
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await raw.execute("DELETE FROM virtual_keys WHERE id = $1", key_id)
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await raw.execute("TRUNCATE virtual_keys")
        assert await raw.fetchval("SELECT count(*) FROM virtual_keys") == 1
    finally:
        await raw.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("key_hash", b"\x01" * 32),
        ("pepper_version", 2),
        ("created_at", NOW - timedelta(days=1)),
    ],
)
async def test_what_identifies_a_key_cannot_change(schema: Schema, column: str, value: Any) -> None:
    await prepare(schema)
    raw = await schema.raw()
    try:
        key_id = await insert(raw)
        update = f"UPDATE virtual_keys SET {column} = $2 WHERE id = $1"  # noqa: S608 - fixed names
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await raw.execute(update, key_id, value)
    finally:
        await raw.close()


async def test_the_id_cannot_change(schema: Schema) -> None:
    await prepare(schema)
    raw = await schema.raw()
    try:
        key_id = await insert(raw)
        with pytest.raises(asyncpg.PostgresError):
            await raw.execute("UPDATE virtual_keys SET id = id + 100 WHERE id = $1", key_id)
    finally:
        await raw.close()


async def test_the_label_limits_and_expiry_can_change(schema: Schema) -> None:
    await prepare(schema)
    raw = await schema.raw()
    try:
        key_id = await insert(raw)
        await raw.execute(
            "UPDATE virtual_keys SET name = 'otro', scopes = '{models}', per_minute_limit = 5, "
            "per_day_limit = NULL, expires_at = NULL WHERE id = $1",
            key_id,
        )
        row = await raw.fetchrow("SELECT name, scopes FROM virtual_keys WHERE id = $1", key_id)
    finally:
        await raw.close()

    assert (row["name"], row["scopes"]) == ("otro", ["models"])


async def test_a_revocation_is_final(schema: Schema) -> None:
    await prepare(schema)
    raw = await schema.raw()
    try:
        key_id = await insert(raw)
        await raw.execute("UPDATE virtual_keys SET revoked_at = $2 WHERE id = $1", key_id, NOW)
        for later in (None, NOW + timedelta(seconds=1)):
            with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                await raw.execute(
                    "UPDATE virtual_keys SET revoked_at = $2 WHERE id = $1", key_id, later
                )
        await raw.execute("UPDATE virtual_keys SET name = 'sigue' WHERE id = $1", key_id)
        revoked = await raw.fetchval("SELECT revoked_at FROM virtual_keys WHERE id = $1", key_id)
    finally:
        await raw.close()

    assert revoked == NOW


# --- The CLI ----------------------------------------------------------------------------------


def _cli_environment(monkeypatch: pytest.MonkeyPatch, schema: Schema, tmp_path: Any) -> None:
    for name in list(os.environ):
        if name.upper().startswith(("ANTIFAZ_", "PG")):
            monkeypatch.delenv(name, raising=False)
    configured = schema.settings()
    assert configured.database_password is not None
    monkeypatch.setenv("ANTIFAZ_DATABASE_HOST", str(configured.database_host))
    monkeypatch.setenv("ANTIFAZ_DATABASE_PORT", str(configured.database_port))
    monkeypatch.setenv("ANTIFAZ_DATABASE_NAME", configured.database_name)
    monkeypatch.setenv("ANTIFAZ_DATABASE_USER", configured.database_user)
    monkeypatch.setenv("ANTIFAZ_DATABASE_PASSWORD", configured.database_password.get_secret_value())
    monkeypatch.setenv("ANTIFAZ_DATABASE_SSLMODE", "disable")
    monkeypatch.chdir(tmp_path)


def test_the_cli_migrates_and_reports_the_status(
    schema: Schema,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _cli_environment(monkeypatch, schema, tmp_path)
    password = schema.cluster.role_password

    before = main(["db", "status"])
    first = capsys.readouterr()
    migrated_code = main(["db", "migrate"])
    second = capsys.readouterr()
    again = main(["db", "migrate"])
    third = capsys.readouterr()
    status = main(["db", "status"])
    fourth = capsys.readouterr()

    assert before == 1
    assert "not_migrated" in first.err
    assert migrated_code == 0
    assert f"schema at version {SCHEMA_VERSION}" in second.out
    assert again == 0
    assert "nothing to apply" in third.out
    assert status == 0
    assert f"schema version: {SCHEMA_VERSION}" in fourth.out
    assert "keys: 0 active, 0 revoked, 0 expired" in fourth.out
    for output in (first, second, third, fourth):
        assert password not in output.out + output.err
