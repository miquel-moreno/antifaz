"""The panel code never holds the admin token itself (issue 53, ADR-0018, invariant 13).

The gateway hands the panel only the token's fingerprint (11b-2a: PanelState). Nothing under
antifaz/web may reach a secret: no get_secret_value(), no `admin_token`, `_secret_value`,
`model_dump` or `__dict__`, no vars(), no getattr() with a name that is not a literal, and no
import of antifaz.config or pydantic (where SecretStr lives). The door's pure pieces are standard
library only (plus antifaz.trusted_networks and each other, themselves standard library only).
Indirect imports are checked too (end of this file): no antifaz.web module loads the settings
through another project module.
"""

import ast
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

import antifaz.trusted_networks
import antifaz.web

WEB = Path(antifaz.web.__file__).parent
SOURCES = sorted(WEB.rglob("*.py"))
# The door's pure pieces (11b-1 and 11b-2a): no framework, no third-party code.
PURE = (
    "forwarded.py",
    "sessions.py",
    "login_limit.py",
    "cookies.py",
    "headers.py",
    "pages.py",
    "csrf.py",
)
SHARED_RULE = Path(antifaz.trusted_networks.__file__)
# A pure piece may use another pure piece (each is checked here itself).
ALLOWED_OWN_MODULES = {"antifaz.trusted_networks", *(f"antifaz.web.{n[:-3]}" for n in PURE)}
FORBIDDEN_NAMES = {"get_secret_value", "admin_token", "_secret_value", "model_dump", "__dict__"}
FORBIDDEN_IMPORTS = ("antifaz.config", "pydantic", "pydantic_settings")


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(tree: ast.Module) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative imports hide what is imported"
            modules.append(node.module or "")
            modules += [f"{node.module}.{alias.name}" for alias in node.names]
    return modules


def _calls_to(tree: ast.Module, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


def test_there_is_code_to_check() -> None:
    names = {path.name for path in SOURCES}
    assert {"__init__.py", "door.py", "routes.py", *PURE} <= names


def test_the_web_package_never_touches_a_secret_by_name() -> None:
    for path in SOURCES:
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Attribute):
                assert node.attr not in FORBIDDEN_NAMES, (path.name, node.attr)
            elif isinstance(node, ast.Name):
                assert node.id not in FORBIDDEN_NAMES, (path.name, node.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in FORBIDDEN_NAMES, (path.name, node.value)


def test_the_web_package_never_calls_vars_nor_a_dynamic_getattr() -> None:
    for path in SOURCES:
        tree = _tree(path)
        assert not _calls_to(tree, "vars"), path.name
        for call in _calls_to(tree, "getattr"):
            name = call.args[1] if len(call.args) > 1 else None
            assert isinstance(name, ast.Constant) and isinstance(name.value, str), path.name


def test_the_web_package_never_imports_the_settings_or_pydantic() -> None:
    for path in SOURCES:
        for module in _imports(_tree(path)):
            for forbidden in FORBIDDEN_IMPORTS:
                assert module != forbidden and not module.startswith(f"{forbidden}."), (
                    path.name,
                    module,
                )


def test_the_pure_pieces_import_only_the_standard_library() -> None:
    for path in [*(WEB / name for name in PURE), SHARED_RULE]:
        for module in _imports(_tree(path)):
            if module in ALLOWED_OWN_MODULES or module.rsplit(".", 1)[0] in ALLOWED_OWN_MODULES:
                assert path != SHARED_RULE
                continue
            assert module.split(".")[0] in sys.stdlib_module_names, (path.name, module)


def test_the_guard_itself_catches_what_it_forbids() -> None:
    """The checks above would see the patterns they ban (so they are not empty passes)."""
    bad = ast.parse(
        "import pydantic\n"
        "from antifaz.config import Settings\n"
        "x = s.admin_token.get_secret_value()\n"
        "y = vars(s)\n"
        "z = getattr(s, name)\n"
        "w = s.__dict__\n"
    )
    imports = _imports(bad)
    assert "pydantic" in imports and "antifaz.config" in imports
    attributes = {n.attr for n in ast.walk(bad) if isinstance(n, ast.Attribute)}
    assert {"admin_token", "get_secret_value", "__dict__"} <= attributes
    assert _calls_to(bad, "vars")
    (dynamic,) = _calls_to(bad, "getattr")
    assert not isinstance(dynamic.args[1], ast.Constant)


# --- Indirect imports: nothing the panel loads reaches the settings ---------------------------
#
# The checks above only see each file's own import lines. The panel could still load
# antifaz.config (and the secrets) through another project module: web/door.py once did,
# through antifaz.api.gate. Two checks close that:
#
# 1. At runtime, in a fresh interpreter per module: importing it leaves antifaz.config and
#    pydantic_settings out of sys.modules, and pydantic too, except for web/routes.py, whose
#    FastAPI router needs pydantic (a third-party dependency, not a project import).
#    `import antifaz` itself does not load the settings, so this check is exact.
# 2. Statically, over the project modules only: the transitive closure of each web module's
#    `antifaz.*` imports (parent packages' __init__ included, relative imports resolved, lazy
#    imports inside functions too) never contains antifaz.config, and no module in it imports
#    pydantic or pydantic_settings itself. This also covers what (1) must allow for routes.py.
#
# Each check has a negative case on a small fake package: a web module that imports a module
# that imports the settings must fail it.

WEB_MODULES = sorted(
    "antifaz.web" if path.stem == "__init__" else f"antifaz.web.{path.stem}" for path in SOURCES
)
LOADED_SETTINGS = ("antifaz.config", "pydantic_settings", "pydantic")
# FastAPI (APIRouter) needs pydantic: the only web module allowed to load it, and only through
# third-party code (check 2 makes sure no project module in its closure imports it).
NEEDS_FASTAPI = {"antifaz.web.routes"}
_PROBE = (
    "import importlib, sys\n"
    "importlib.import_module(sys.argv[1])\n"
    "print(' '.join(m for m in sys.argv[2:] if m in sys.modules))\n"
)


def _loaded(module: str, watched: Sequence[str], extra_path: Path | None = None) -> list[str]:
    """Which of `watched` a fresh interpreter has loaded after importing `module`."""
    env = dict(os.environ)
    if extra_path is not None:
        env["PYTHONPATH"] = os.pathsep.join([str(extra_path), env.get("PYTHONPATH", "")])
    result = subprocess.run(  # noqa: S603 - a fixed program, never a shell
        [sys.executable, "-c", _PROBE, module, *watched],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        timeout=120,
    )
    return result.stdout.split()


def test_there_are_web_modules_to_check() -> None:
    assert {"antifaz.web", "antifaz.web.door", "antifaz.web.routes"} <= set(WEB_MODULES)


@pytest.mark.parametrize("module", WEB_MODULES)
def test_importing_a_web_module_never_loads_the_settings(module: str) -> None:
    loaded = _loaded(module, LOADED_SETTINGS)

    allowed = {"pydantic"} if module in NEEDS_FASTAPI else set()
    assert set(loaded) <= allowed, (module, loaded)


def _module_file(root: Path, name: str) -> Path | None:
    base = root.joinpath(*name.split("."))
    for candidate in (base / "__init__.py", base.with_suffix(".py")):
        if candidate.is_file():
            return candidate
    return None


def _with_parents(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts) + 1)]


def _imported_names(path: Path, name: str) -> list[str]:
    """Every module a file imports (lazy ones too), relative imports resolved."""
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*anchor, *([node.module] if node.module else [])])
            else:
                base = node.module or ""
            names.append(base)
            names += [f"{base}.{alias.name}" for alias in node.names]  # maybe a submodule
    return names


def _project_closure(root: Path, package: str, start: str) -> tuple[set[str], set[str]]:
    """(project modules reached from `start`, third-party top-level names they import)."""
    seen: set[str] = set()
    external: set[str] = set()
    queue = _with_parents(start)
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        path = _module_file(root, name)
        if path is None:
            continue  # "from pkg.mod import function": not a module
        seen.add(name)
        for imported in _imported_names(path, name):
            if imported == package or imported.startswith(f"{package}."):
                queue += [n for n in _with_parents(imported) if n not in seen]
            elif imported:
                external.add(imported.split(".")[0])
    return seen, external


SRC = WEB.parent.parent  # src/


def _settings_in_closure(root: Path, package: str, start: str) -> list[str]:
    modules, external = _project_closure(root, package, start)
    problems = sorted(m for m in modules if m == f"{package}.config")
    problems += sorted(external & {"pydantic", "pydantic_settings"})
    return problems


@pytest.mark.parametrize("module", WEB_MODULES)
def test_no_project_import_of_a_web_module_reaches_the_settings(module: str) -> None:
    modules, _ = _project_closure(SRC, "antifaz", module)
    assert module in modules  # the walk found the module itself: not an empty pass

    assert _settings_in_closure(SRC, "antifaz", module) == []


def _fake_package(tmp_path: Path, door_imports: str) -> Path:
    """fakepkg: config.py (the settings), mid.py (imports them), web/{clean,door}.py."""
    package = tmp_path / "fakepkg"
    (package / "web").mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "config.py").write_text("import json\nSECRET = 1\n", encoding="utf-8")
    (package / "mid.py").write_text("from fakepkg.config import SECRET\n", encoding="utf-8")
    (package / "web" / "__init__.py").write_text("", encoding="utf-8")
    (package / "web" / "clean.py").write_text("import json\n", encoding="utf-8")
    (package / "web" / "door.py").write_text(door_imports, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "door_imports",
    [
        "from fakepkg.mid import SECRET\n",  # absolute, one module in between
        "from .. import mid\n",  # relative
        "def f():\n    import fakepkg.mid\n",  # lazy, inside a function
    ],
)
def test_the_indirect_checks_catch_a_settings_import_one_module_away(
    tmp_path: Path, door_imports: str
) -> None:
    root = _fake_package(tmp_path, door_imports)

    assert _settings_in_closure(root, "fakepkg", "fakepkg.web.door") == ["fakepkg.config"]
    assert _settings_in_closure(root, "fakepkg", "fakepkg.web.clean") == []
    if not door_imports.startswith("def"):  # a lazy import only loads when called
        assert _loaded("fakepkg.web.door", ["fakepkg.config"], root) == ["fakepkg.config"]
    assert _loaded("fakepkg.web.clean", ["fakepkg.config"], root) == []


def test_the_static_check_catches_pydantic_one_module_away(tmp_path: Path) -> None:
    root = _fake_package(tmp_path, "import fakepkg.models\n")
    (root / "fakepkg" / "models.py").write_text(
        "from pydantic import BaseModel\n", encoding="utf-8"
    )

    assert _settings_in_closure(root, "fakepkg", "fakepkg.web.door") == ["pydantic"]
