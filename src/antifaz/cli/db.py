"""`antifaz db migrate` and `antifaz db status` (issue 121, PR 8b-1, ADR-0021).

- migrate: creates or updates the tables of the virtual keys (forward only, under an advisory
  lock; antifaz/db/migrate.py). `serve` never migrates.
- status: the schema version and how many keys are active, revoked and expired (SQL counts).

Settings as in doctor: the .env of --path (default: the current folder) with ANTIFAZ_*
environment variables on top. Exit codes as in doctor: 0 fine, 1 the database has a problem
(a closed reason with its fixed text), 2 usage or configuration (PostgreSQL not configured, or a
setting check_database_settings refuses). Every message is fixed text naming variables, never a
value, an asyncpg error or a host.

This module only defines the parser and run(): it imports antifaz.db (and so asyncpg) INSIDE
run(), so the rest of the CLI never loads it (tests/unit/test_db_imports.py).
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# doctor's reader: it names the variables of a validation error, never their values.
from antifaz.cli.doctor import load_settings
from antifaz.config import (
    Settings,
    UnsafeConfigError,
    check_database_settings,
    database_tls_warning,
)

NOT_CONFIGURED = (
    "antifaz db: PostgreSQL is not configured: set ANTIFAZ_DATABASE_HOST and "
    "ANTIFAZ_DATABASE_PASSWORD (virtual keys are off without it)"
)


def add_parser(commands: "argparse._SubParsersAction[Any]") -> None:
    command = commands.add_parser(
        "db",
        help="PostgreSQL for the virtual keys: create or update its tables (migrate) or check "
        "it (status); never prints a value",
    )
    actions = command.add_subparsers(dest="db_command", required=True)
    for name, help_text in (
        ("migrate", "create or update the tables of the virtual keys (forward only)"),
        ("status", "check the database and count the active, revoked and expired keys"),
    ):
        action = actions.add_parser(name, help=help_text)
        action.add_argument(
            "--path", default=".", help="folder with the .env to read (default: current folder)"
        )


def _settings(folder: Path) -> tuple[Settings | None, str]:
    """The settings, or the fixed message (naming variables) of why they cannot be used."""
    settings, problem = load_settings(folder)
    if settings is None:
        return None, f"antifaz db: configuration: {problem}"
    if settings.database_host is None:
        return None, NOT_CONFIGURED
    try:
        check_database_settings(settings)
    except UnsafeConfigError as error:  # its message names the variable, never the value
        return None, f"antifaz db: configuration: {error}"
    return settings, ""


def run(args: argparse.Namespace) -> int:
    # Only here: `antifaz db` is the one command that loads asyncpg (decision D1).
    from antifaz import db

    folder = Path(args.path)
    if not folder.is_dir():
        print("antifaz db: the folder given with --path does not exist", file=sys.stderr)
        return 2
    settings, problem = _settings(folder)
    if settings is None:
        print(problem, file=sys.stderr)
        return 2
    warning = database_tls_warning(settings)
    if warning is not None:  # before connecting: the password would travel in clear text
        print(f"antifaz db: warning: {warning}", file=sys.stderr)
    work = _migrate if args.db_command == "migrate" else _status
    failure = asyncio.run(work(settings))
    if failure is not None:
        print(f"antifaz db: {db.PROBLEM_TEXTS[failure]}", file=sys.stderr)
        return 1
    return 0


async def _migrate(settings: Settings) -> Any:
    """None when done (and printed), else the closed reason."""
    from antifaz import db

    try:
        session = await db.connect(settings)
    except db.DatabaseError as error:
        return error.problem
    try:
        result = await db.migrate(session)
    except db.DatabaseError as error:
        return error.problem
    finally:
        await session.close()
    if result.applied:
        applied = ", ".join(f"{version:04d}" for version in result.applied)
        print(f"antifaz db migrate: applied {applied}; schema at version {result.version}")
    else:
        print(f"antifaz db migrate: nothing to apply; schema at version {result.version}")
    return None


async def _status(settings: Settings) -> Any:
    """None when the database is ready (and its status printed), else the closed reason."""
    from antifaz import db

    try:
        session = await db.connect(settings)
    except db.DatabaseError as error:
        return error.problem
    try:
        check = await db.check_database(session)
        if not check.ready:
            return check.problem
        counts = await db.key_counts(session, datetime.now(UTC))
    except db.DatabaseError as error:
        return error.problem
    finally:
        await session.close()
    print(
        "antifaz db status\n"
        f"  schema version: {check.version}\n"
        f"  keys: {counts.active} active, {counts.revoked} revoked, {counts.expired} expired"
    )
    return None


__all__ = ["NOT_CONFIGURED", "add_parser", "run"]
