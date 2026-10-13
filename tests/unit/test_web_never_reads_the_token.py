"""The panel code never holds the admin token itself (issue 53, ADR-0018, invariant 13).

The gateway hands the panel only the token's fingerprint (11b-2). Nothing under antifaz/web
may reach a secret: no get_secret_value(), no `admin_token`, `_secret_value`, `model_dump` or
`__dict__`, no vars(), no getattr() with a name that is not a literal, and no import of
antifaz.config or pydantic (where SecretStr lives). The door's pure pieces are standard
library only (plus antifaz.trusted_networks, itself standard library only).
"""

import ast
import sys
from pathlib import Path

import antifaz.trusted_networks
import antifaz.web

WEB = Path(antifaz.web.__file__).parent
SOURCES = sorted(WEB.rglob("*.py"))
# The door's pure pieces (11b-1): no framework, no third-party code.
PURE = ("forwarded.py", "sessions.py", "login_limit.py")
SHARED_RULE = Path(antifaz.trusted_networks.__file__)
ALLOWED_OWN_MODULES = {"antifaz.trusted_networks"}
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
    assert {"__init__.py", "forwarded.py", "sessions.py", "login_limit.py"} <= names


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
