"""The panel code never holds the admin token itself (issue 53, ADR-0018, invariant 13).

The gateway hands the panel only the token's fingerprint (11b-2): nothing under antifaz/web may
call get_secret_value() or read an `admin_token` attribute or name. The panel's pure pieces are
standard library only.
"""

import ast
import sys
from pathlib import Path

import antifaz.web

WEB = Path(antifaz.web.__file__).parent
SOURCES = sorted(WEB.rglob("*.py"))
# The door's pure pieces (11b-1): no framework, no third-party code.
PURE = ("forwarded.py", "sessions.py", "login_limit.py")


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_there_is_code_to_check() -> None:
    names = {path.name for path in SOURCES}
    assert {"__init__.py", "forwarded.py", "sessions.py", "login_limit.py"} <= names


def test_the_web_package_never_calls_get_secret_value() -> None:
    for path in SOURCES:
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Attribute):
                assert node.attr != "get_secret_value", path.name
            if isinstance(node, ast.Name):
                assert node.id != "get_secret_value", path.name


def test_the_web_package_never_reads_the_admin_token() -> None:
    for path in SOURCES:
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Attribute):
                assert node.attr != "admin_token", path.name
            elif isinstance(node, ast.Name):
                assert node.id != "admin_token", path.name
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value != "admin_token", path.name  # getattr(settings, "admin_token")


def test_the_pure_pieces_import_only_the_standard_library() -> None:
    for path in (WEB / name for name in PURE):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, path.name
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                top = module.split(".")[0]
                assert top in sys.stdlib_module_names, (path.name, module)
