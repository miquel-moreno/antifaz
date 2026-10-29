"""Only `antifaz db` (and, from 8c, `antifaz keys`) loads the database code (issue 121).

PostgreSQL is optional: the keys library, the settings, the gateway, the panel and the rest of
the CLI never load asyncpg or antifaz.db (decision D1). Two checks, each with a negative case:

1. In a fresh interpreter: importing each module, or running `antifaz scan -`, `antifaz doctor`
   and `antifaz --help`, leaves asyncpg and antifaz.db out of sys.modules; `antifaz db status`
   does load them (so the check is not an empty pass).
2. Statically: the closure of the module-level imports (parents' __init__ included, relative
   imports resolved) of those modules never reaches antifaz.db or asyncpg, and a lazy import of
   either (inside a function) only appears in the modules allowed to have one.
"""

import ast
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

import antifaz.web

SRC = Path(antifaz.web.__file__).parents[1].parent  # src/
DATABASE_MODULES = ("asyncpg", "antifaz.db")
WEB_MODULES = sorted(
    "antifaz.web" if path.stem == "__init__" else f"antifaz.web.{path.stem}"
    for path in (SRC / "antifaz" / "web").glob("*.py")
)
FREE_MODULES = [
    "antifaz.keys",
    "antifaz.config",
    "antifaz.cli",
    "antifaz.cli.doctor",
    "antifaz.api.app",
    *WEB_MODULES,
]
# The only modules that may import the database code, and only inside a function.
LAZY_IMPORTERS = {"antifaz.cli.db"}

_IMPORT_PROBE = (
    "import importlib, sys\n"
    "importlib.import_module(sys.argv[1])\n"
    "print(' '.join(m for m in sys.argv[2:] if m in sys.modules))\n"
)
_CLI_PROBE = (
    "import io, sys\n"
    "watched = sys.argv[1].split(',')\n"
    "sys.stdin = io.TextIOWrapper(io.BytesIO('Hola, soy Ana.'.encode()), encoding='utf-8')\n"
    "from antifaz.cli import main\n"
    "out = sys.stdout\n"
    "sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')\n"
    "sys.stderr = io.StringIO()\n"
    "try:\n"
    "    main(sys.argv[2:])\n"
    "except SystemExit:\n"
    "    pass\n"
    "out.write(' '.join(m for m in watched if m in sys.modules) + '\\n')\n"
)


def _clean_environment(extra_path: Path | None = None) -> dict[str, str]:
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith(("ANTIFAZ_", "PG"))
    }
    if extra_path is not None:
        env["PYTHONPATH"] = os.pathsep.join([str(extra_path), env.get("PYTHONPATH", "")])
    return env


def _run(arguments: Sequence[str], cwd: Path, extra_path: Path | None = None) -> list[str]:
    result = subprocess.run(  # noqa: S603 - a fixed program, never a shell
        [sys.executable, "-c", *arguments],
        capture_output=True,
        text=True,
        check=True,
        env=_clean_environment(extra_path),
        cwd=cwd,
        timeout=120,
    )
    return result.stdout.split()


def loaded_after_import(module: str, tmp_path: Path, extra_path: Path | None = None) -> list[str]:
    return _run([_IMPORT_PROBE, module, *DATABASE_MODULES], tmp_path, extra_path)


def loaded_after_cli(argv: Sequence[str], tmp_path: Path) -> list[str]:
    """Run `antifaz <argv>` in a fresh interpreter, in an empty folder (no .env)."""
    return _run([_CLI_PROBE, ",".join(DATABASE_MODULES), *argv], tmp_path)


@pytest.mark.parametrize("module", FREE_MODULES)
def test_importing_never_loads_the_database_code(module: str, tmp_path: Path) -> None:
    assert loaded_after_import(module, tmp_path) == []


@pytest.mark.parametrize(
    "argv",
    [
        ["scan", "-"],
        ["mask", "-"],
        ["--help"],
        ["doctor", "--url", "http://127.0.0.1:9", "--timeout", "0.2"],
    ],
    ids=["scan", "mask", "help", "doctor"],
)
def test_the_other_commands_never_load_the_database_code(argv: list[str], tmp_path: Path) -> None:
    assert loaded_after_cli(argv, tmp_path) == []


def test_antifaz_db_status_loads_the_database_code(tmp_path: Path) -> None:
    assert loaded_after_cli(["db", "status"], tmp_path) == list(DATABASE_MODULES)


# --- 2. Statically ----------------------------------------------------------------------------


def _module_file(root: Path, name: str) -> Path | None:
    base = root.joinpath(*name.split("."))
    for candidate in (base / "__init__.py", base.with_suffix(".py")):
        if candidate.is_file():
            return candidate
    return None


def _with_parents(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts) + 1)]


def _imports(path: Path, name: str) -> tuple[list[str], list[str]]:
    """(module-level imports, imports inside a function or class body), relative resolved."""
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    top_level: list[str] = []
    lazy: list[str] = []

    def names_of(node: ast.Import | ast.ImportFrom) -> list[str]:
        if isinstance(node, ast.Import):
            return [alias.name for alias in node.names]
        if node.level:
            anchor = package.split(".")[: len(package.split(".")) - node.level + 1]
            base = ".".join([*anchor, *([node.module] if node.module else [])])
        else:
            base = node.module or ""
        return [base, *(f"{base}.{alias.name}" for alias in node.names)]

    def visit(node: ast.AST, inside: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import | ast.ImportFrom):
                (lazy if inside else top_level).extend(names_of(child))
            nested = inside or isinstance(
                child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
            )
            visit(child, nested)

    visit(tree, inside=False)
    return top_level, lazy


def _is_database(name: str, package: str) -> bool:
    return (
        name.split(".")[0] == "asyncpg"
        or name == f"{package}.db"
        or name.startswith(f"{package}.db.")
    )


def eager_closure(root: Path, package: str, start: str) -> set[str]:
    """Every module reached from `start` through module-level imports (project modules are
    followed; third-party names are kept as they are, not followed)."""
    seen: set[str] = set()
    reached: set[str] = set()
    queue = _with_parents(start)
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        path = _module_file(root, name)
        if path is None:
            continue
        reached.add(name)
        top_level, _ = _imports(path, name)
        for imported in top_level:
            if imported == package or imported.startswith(f"{package}."):
                queue += [n for n in _with_parents(imported) if n not in seen]
            elif imported:
                reached.add(imported)
    return reached


def lazy_database_importers(root: Path, package: str) -> set[str]:
    """Project modules (outside package.db) with a database import inside a function."""
    found: set[str] = set()
    for path in sorted((root / package).rglob("*.py")):
        parts = path.relative_to(root).with_suffix("").parts
        name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        if _is_database(name, package):
            continue
        _, lazy = _imports(path, name)
        if any(_is_database(imported, package) for imported in lazy):
            found.add(name)
    return found


@pytest.mark.parametrize("module", FREE_MODULES)
def test_no_module_level_import_reaches_the_database_code(module: str) -> None:
    reached = eager_closure(SRC, "antifaz", module)

    assert module in reached  # the walk found the module itself: not an empty pass
    assert sorted(name for name in reached if _is_database(name, "antifaz")) == []


def test_the_cli_reaches_the_db_command_only_lazily() -> None:
    assert "antifaz.cli.db" in eager_closure(SRC, "antifaz", "antifaz.cli")
    assert lazy_database_importers(SRC, "antifaz") == LAZY_IMPORTERS


def _fake_package(tmp_path: Path, door: str) -> Path:
    package = tmp_path / "fakepkg"
    (package / "db").mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "db" / "__init__.py").write_text("import json\n", encoding="utf-8")
    (package / "mid.py").write_text("from fakepkg.db import json\n", encoding="utf-8")
    (package / "door.py").write_text(door, encoding="utf-8")
    (package / "clean.py").write_text("import json\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "door",
    [
        "from fakepkg.mid import json\n",  # one module in between
        "from . import mid\n",  # relative
        "import asyncpg\n",  # the driver itself
    ],
)
def test_the_static_check_catches_an_eager_import_one_module_away(
    tmp_path: Path, door: str
) -> None:
    root = _fake_package(tmp_path, door)

    reached = eager_closure(root, "fakepkg", "fakepkg.door")
    assert any(_is_database(name, "fakepkg") for name in reached)
    assert not any(
        _is_database(n, "fakepkg") for n in eager_closure(root, "fakepkg", "fakepkg.clean")
    )
    if "asyncpg" not in door:
        assert loaded_after_import("fakepkg.door", tmp_path, root) == []  # watched: real names
        probe = _run([_IMPORT_PROBE, "fakepkg.door", "fakepkg.db"], tmp_path, root)
        assert probe == ["fakepkg.db"]


def test_the_static_check_sees_a_lazy_import_and_does_not_follow_it(tmp_path: Path) -> None:
    root = _fake_package(tmp_path, "def run():\n    import fakepkg.db\n")

    assert not any(
        _is_database(n, "fakepkg") for n in eager_closure(root, "fakepkg", "fakepkg.door")
    )
    assert lazy_database_importers(root, "fakepkg") == {"fakepkg.door"}
