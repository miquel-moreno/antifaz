"""A real PostgreSQL 16 for the tests marked `postgres` (issue 121, PR 8b-1).

Only with --postgres (`make test-postgres`, and CI's `quality` job). The server comes from the
TEST_POSTGRES_* variables (never ANTIFAZ_*: a developer's .env must not point the tests at a
real database): HOST, PORT, USER and PASSWORD of a role that can create roles and databases,
and DATABASE, the maintenance database to connect to.

Isolation:
- per session, a database `antifaz_test_<random>` and a NON-superuser role that owns it, with a
  random password made here (the app connects as that role, as Antifaz would);
- per test, its own schema, made the role's search_path in that database before the test
  connects (so every test starts from an empty schema);
- a guard refuses any database whose name does not start with `antifaz_test`, before anything
  is created, used or dropped.

With --postgres and no reachable server the tests FAIL (pytest.fail), never skip.
"""

import asyncio
import os
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from antifaz.config import Settings
from tests.conftest import forbid_network

PREFIX = "antifaz_test"
VARIABLES = ("HOST", "PORT", "USER", "PASSWORD", "DATABASE")


def guard(database: str) -> str:
    """Refuse any database that is not a throwaway test database."""
    if not database.startswith(PREFIX) or not database.replace("_", "").isalnum():
        pytest.fail("the PostgreSQL tests only touch databases named antifaz_test*")
    return database


@dataclass(frozen=True)
class Server:
    host: str
    port: int
    user: str
    password: str
    database: str

    async def connect(self, database: str | None = None) -> Any:
        return await asyncpg.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=database or self.database,
            ssl=False,
            timeout=10,
        )


@dataclass(frozen=True)
class Cluster:
    server: Server
    database: str
    ascii_database: str
    role: str
    role_password: str

    def settings(self, database: str | None = None) -> Settings:
        return Settings(  # type: ignore[call-arg]
            _env_file=None,
            database_host=self.server.host,
            database_port=self.server.port,
            database_name=guard(database or self.database),
            database_user=self.role,
            database_password=SecretStr(self.role_password),
            database_sslmode="disable",
            database_timeout_seconds=10,
        )

    async def app_connection(self, database: str | None = None) -> Any:
        """A raw asyncpg connection as the app role, for direct SQL in the tests."""
        return await asyncpg.connect(
            host=self.server.host,
            port=self.server.port,
            user=self.role,
            password=self.role_password,
            database=guard(database or self.database),
            ssl=False,
            timeout=10,
        )


def _server() -> Server:
    values = {name: os.environ.get(f"TEST_POSTGRES_{name}", "") for name in VARIABLES}
    missing = [f"TEST_POSTGRES_{name}" for name, value in values.items() if not value]
    if missing:
        pytest.fail("--postgres needs " + ", ".join(missing) + " (see CONTRIBUTING.md)")
    return Server(
        host=values["HOST"],
        port=int(values["PORT"]),
        user=values["USER"],
        password=values["PASSWORD"],
        database=values["DATABASE"],
    )


async def _create(cluster: Cluster) -> None:
    connection = await cluster.server.connect()
    try:
        # Names and password are hex made here: safe to write into the statements.
        await connection.execute(
            f"CREATE ROLE {cluster.role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            f"NOINHERIT PASSWORD '{cluster.role_password}'"
        )
        await connection.execute(
            f"CREATE DATABASE {guard(cluster.database)} OWNER {cluster.role} "
            "ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0"
        )
        await connection.execute(
            f"CREATE DATABASE {guard(cluster.ascii_database)} OWNER {cluster.role} "
            "ENCODING 'SQL_ASCII' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0"
        )
    finally:
        await connection.close(timeout=10)


async def _drop(cluster: Cluster) -> None:
    connection = await cluster.server.connect()
    try:
        for database in (cluster.database, cluster.ascii_database):
            await connection.execute(f"DROP DATABASE IF EXISTS {guard(database)} WITH (FORCE)")
        await connection.execute(f"DROP ROLE IF EXISTS {cluster.role}")
    finally:
        await connection.close(timeout=10)


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test server is on 127.0.0.1 (CI and `make test-postgres`): nothing else is reached."""
    forbid_network(monkeypatch)


@pytest.fixture(scope="session")
def cluster(request: pytest.FixtureRequest) -> Iterator[Cluster]:
    if not request.config.getoption("--postgres"):
        pytest.skip("needs PostgreSQL: run `make test-postgres`")
    suffix = secrets.token_hex(6)
    made = Cluster(
        server=_server(),
        database=guard(f"{PREFIX}_{suffix}"),
        ascii_database=guard(f"{PREFIX}_{suffix}_ascii"),
        role=f"{PREFIX}_app_{suffix}",
        role_password=secrets.token_hex(24),
    )
    try:
        asyncio.run(_create(made))
    except (OSError, asyncpg.PostgresError, TimeoutError) as error:
        pytest.fail(f"cannot prepare the test PostgreSQL: {type(error).__name__}")
    try:
        yield made
    finally:
        asyncio.run(_drop(made))


@dataclass(frozen=True)
class Schema:
    cluster: Cluster
    name: str
    database: str

    def settings(self) -> Settings:
        return self.cluster.settings(self.database)

    async def raw(self) -> Any:
        """A raw connection as the app role, whose search_path is this test's schema."""
        return await self.cluster.app_connection(self.database)


async def _schema_for(cluster: Cluster, database: str) -> AsyncIterator[Schema]:
    name = f"t_{secrets.token_hex(6)}"
    admin = await cluster.server.connect(guard(database))
    try:
        await admin.execute(f"CREATE SCHEMA {name} AUTHORIZATION {cluster.role}")
        await admin.execute(
            f"ALTER ROLE {cluster.role} IN DATABASE {database} SET search_path = {name}"
        )
        yield Schema(cluster, name, database)
    finally:
        await admin.execute(f"ALTER ROLE {cluster.role} IN DATABASE {database} RESET search_path")
        await admin.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
        await admin.close(timeout=10)


@pytest.fixture
async def schema(cluster: Cluster) -> AsyncIterator[Schema]:
    """An empty schema of the UTF8 test database, the app role's search_path in it."""
    async for made in _schema_for(cluster, cluster.database):
        yield made


@pytest.fixture
async def ascii_schema(cluster: Cluster) -> AsyncIterator[Schema]:
    """The same in the SQL_ASCII database."""
    async for made in _schema_for(cluster, cluster.ascii_database):
        yield made
