"""How the panel's real-browser checks are set up (issue 53, PR 11c-2a), and that they never ship.

Playwright and axe-playwright-python live in the `panel` dependency group: locked in uv.lock (so
Dependabot and `make audit` see them), never a default group, never in what ships (`uv export
--no-dev` with every extra) and never selected by the Dockerfile. `make e2e` checks the image
itself (tests/e2e/test_image_imports.py).
"""

import re
import tomllib
from pathlib import Path
from typing import Any

from scripts import check_licenses

from tests.e2e.image_probes import BROWSER_CHECK_PACKAGES

REPO = Path(__file__).resolve().parents[2]
PYPROJECT: dict[str, Any] = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
LOCK: dict[str, Any] = tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))
PLAYWRIGHT = "1.63.0"
AXE = "0.1.8"


def _locked(name: str) -> str:
    (version,) = [p["version"] for p in LOCK["package"] if p["name"] == name]
    return str(version)


def test_the_panel_group_pins_playwright_and_axe() -> None:
    assert PYPROJECT["dependency-groups"]["panel"] == [
        f"playwright=={PLAYWRIGHT}",
        f"axe-playwright-python=={AXE}",
    ]


def test_the_panel_group_is_never_installed_by_default() -> None:
    # `uv sync`, `make check` and the Dockerfile's `--no-dev` only drop `dev`: a default panel
    # group would come in everywhere.
    assert PYPROJECT["tool"]["uv"].get("default-groups", ["dev"]) == ["dev"]


def test_uv_lock_holds_the_panel_group_with_the_pinned_versions() -> None:
    (antifaz,) = [p for p in LOCK["package"] if p["name"] == "antifaz"]
    names = {d["name"] for d in antifaz["dev-dependencies"]["panel"]}
    assert names == {"playwright", "axe-playwright-python"}
    assert _locked("playwright") == PLAYWRIGHT
    assert _locked("axe-playwright-python") == AXE


def test_what_ships_never_brings_the_browser_check_packages() -> None:
    panel = {name for name, _ in check_licenses._exported(["--only-group", "panel"])}
    assert panel >= BROWSER_CHECK_PACKAGES  # the check below is not empty
    shipped = {name for name, _ in check_licenses.runtime_dependencies()}
    assert not shipped & BROWSER_CHECK_PACKAGES


def test_the_dockerfile_never_selects_a_dependency_group() -> None:
    dockerfile = re.sub(r"\\\n\s*", " ", (REPO / "Dockerfile").read_text(encoding="utf-8"))
    syncs = [
        line for line in dockerfile.splitlines() if line.startswith("RUN") and "uv sync" in line
    ]
    assert syncs
    for line in syncs:
        assert "--no-dev" in line, line
        for flag in ("--group", "--all-groups", "--only-group", "--extra", "--all-extras"):
            assert flag not in line, line


def test_the_design_script_pins_the_same_versions_as_the_lock() -> None:
    script = (REPO / "scripts" / "check_design.py").read_text(encoding="utf-8")
    assert f'"playwright=={_locked("playwright")}"' in script
    assert f'"axe-playwright-python=={_locked("axe-playwright-python")}"' in script


def test_the_browser_marker_is_registered() -> None:
    markers = PYPROJECT["tool"]["pytest"]["ini_options"]["markers"]
    assert any(marker.startswith("browser:") for marker in markers)


def test_ruff_treats_the_browser_tests_like_the_other_tests() -> None:
    ignores = PYPROJECT["tool"]["ruff"]["lint"]["per-file-ignores"]
    assert set(ignores["tests/browser/**"]) == set(ignores["tests/**"])
