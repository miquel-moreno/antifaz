"""Static rules of the database code (issue 121, PR 8b-1, ADR-0021), read from its AST.

1. Every asyncpg call (connect, execute, executemany, fetch, fetchrow, fetchval, close) passes
   `timeout=`: a database that stops answering never holds the gateway or the CLI.
2. Every query is a module-level constant written as `Sql("...")` (a plain literal), never an
   f-string, `%`, `.format()`, a concatenation or a local variable: values only travel as
   query arguments ($1, $2...), so nothing a user typed becomes SQL. Two exceptions, both
   fixed by name: the adapter's own methods forward their `query` parameter (typed Sql), and
   the runner executes `migration.sql`, the text of a file shipped in the package.
3. Only antifaz/db/connect.py imports asyncpg.

Each rule has a negative case on a small source text.
"""

import ast
from pathlib import Path

import pytest

import antifaz.db

DB = Path(antifaz.db.__file__).parent
SOURCES = sorted(DB.glob("*.py"))
CALLS_WITH_TIMEOUT = frozenset(
    {"connect", "execute", "executemany", "fetch", "fetchrow", "fetchval", "close"}
)
QUERY_CALLS = frozenset({"execute", "executemany", "fetch", "fetchrow", "fetchval"})
ADAPTER = "connect.py"
# (file, the expression allowed as a query besides a constant)
FORWARDED = {(ADAPTER, "query"), ("migrate.py", "migration.sql")}


def _constants(tree: ast.Module) -> tuple[set[str], list[str]]:
    """Module-level Sql constants, and the problems of every Sql(...) built anywhere."""
    names: set[str] = set()
    problems: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Sql"
            and not (
                len(node.args) == 1
                and not node.keywords
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            )
        ):
            problems.append(f"line {node.lineno}: Sql() takes one plain string literal")
    for statement in tree.body:
        if isinstance(statement, ast.Assign | ast.AnnAssign):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            value = statement.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "Sql"
            ):
                names |= {target.id for target in targets if isinstance(target, ast.Name)}
    return names, problems


def problems_of(source: str, file_name: str) -> list[str]:
    tree = ast.parse(source)
    constants, problems = _constants(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom) and file_name != ADAPTER:
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if any(module.split(".")[0] == "asyncpg" for module in modules):
                problems.append(f"line {node.lineno}: only {ADAPTER} imports asyncpg")
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        method = node.func.attr
        if method in CALLS_WITH_TIMEOUT and not any(k.arg == "timeout" for k in node.keywords):
            problems.append(f"line {node.lineno}: {method}() without timeout=")
        if method not in QUERY_CALLS:
            continue
        if not node.args:
            problems.append(f"line {node.lineno}: {method}() without a query")
            continue
        query = node.args[0]
        if isinstance(query, ast.Name) and query.id in constants:
            continue
        if (file_name, ast.unparse(query)) in FORWARDED:
            continue
        problems.append(f"line {node.lineno}: {method}() takes {ast.unparse(query)!r}")
    return problems


def test_there_is_database_code_to_check() -> None:
    names = {path.name for path in SOURCES}

    assert {"__init__.py", "connect.py", "checks.py", "migrate.py"} <= names


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_the_database_code_follows_the_rules(path: Path) -> None:
    assert problems_of(path.read_text(encoding="utf-8"), path.name) == []


def test_the_adapter_and_the_runner_really_make_calls() -> None:
    """Not an empty pass: the rules looked at real calls."""
    calls = 0
    for path in SOURCES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in QUERY_CALLS
            ):
                calls += 1
    assert calls >= 5


GOOD = """
COUNT = Sql("SELECT count(*) FROM virtual_keys WHERE id = $1")
async def count(session, key_id):
    return await session.fetchval(COUNT, key_id, timeout=5)
"""


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (GOOD, []),
        (
            'async def f(s, t):\n    return await s.fetchval(f"SELECT {t}", timeout=1)\n',
            ["fetchval() takes"],
        ),
        (
            'async def f(s, t):\n    return await s.execute("SELECT %s" % t, timeout=1)\n',
            ["execute() takes"],
        ),
        (
            'async def f(s, t):\n    return await s.fetch("SELECT {}".format(t), timeout=1)\n',
            ["fetch() takes"],
        ),
        (
            'async def f(s, t):\n    return await s.fetchrow("SELECT " + t, timeout=1)\n',
            ["fetchrow() takes"],
        ),
        (
            'async def f(s):\n    q = Sql("SELECT 1")\n    return await s.fetchval(q, timeout=1)\n',
            ["fetchval() takes"],
        ),
        (
            'async def f(s):\n    return await s.fetchval("SELECT 1", timeout=1)\n',
            ["fetchval() takes"],
        ),
        ('Q = Sql(f"SELECT {x}")\n', ["Sql() takes one plain string literal"]),
        ('Q = Sql("SELECT " + x)\n', ["Sql() takes one plain string literal"]),
        (
            'Q = Sql("SELECT 1")\nasync def f(s):\n    return await s.fetchval(Q)\n',
            ["fetchval() without timeout="],
        ),
        ("async def f(s):\n    await s.close()\n", ["close() without timeout="]),
        (
            "import asyncpg\nasync def f():\n    return await asyncpg.connect(host='h')\n",
            ["only connect.py imports asyncpg", "connect() without timeout="],
        ),
        ("from asyncpg import exceptions\n", ["only connect.py imports asyncpg"]),
    ],
)
def test_the_rules_catch_each_mistake(source: str, expected: list[str]) -> None:
    found = problems_of(source, "checks.py")

    assert len(found) == len(expected), found
    for problem, wanted in zip(found, expected, strict=True):
        assert wanted in problem


def test_a_forwarded_query_is_only_allowed_where_it_is_named() -> None:
    source = "async def f(s, query):\n    return await s.fetchval(query, timeout=1)\n"

    assert problems_of(source, ADAPTER) == []
    assert problems_of(source, "checks.py") != []
