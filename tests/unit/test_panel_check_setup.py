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

import yaml
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


# --- make panel-check and its CI job ------------------------------------------------------------

MAKEFILE = (REPO / "Makefile").read_text(encoding="utf-8")
CI: dict[str, Any] = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
JOB: dict[str, Any] = CI["jobs"].get("panel-browser", {})
STEPS: list[dict[str, Any]] = JOB.get("steps", [])
JOB_TEXT = yaml.safe_dump(JOB)
PANEL_CHECK = (
    "uv run --frozen --group panel pytest tests/browser -vv --no-cov --panel -p no:cacheprovider"
)


def _recipe(target: str) -> str:
    match = re.search(rf"^{target}:.*\n((?:\t.*\n)+)", MAKEFILE, re.MULTILINE)
    assert match, target
    return match.group(1)


def test_make_panel_check_runs_only_the_browser_tests() -> None:
    recipe = _recipe("panel-check")
    assert PANEL_CHECK in recipe
    assert "$(if $(PANEL_BROWSERS),--panel-browser $(PANEL_BROWSERS),)" in recipe
    phony = re.search(r"^\.PHONY:(.*)$", MAKEFILE, re.MULTILINE)
    assert phony and "panel-check" in phony.group(1).split()
    assert re.search(r'^\t@echo "panel-check ', MAKEFILE, re.MULTILINE)


def test_make_check_does_not_run_the_browsers() -> None:
    (check,) = re.findall(r"^check:(.*)$", MAKEFILE, re.MULTILINE)
    assert "panel" not in check
    assert "--group panel" not in _recipe("test")


def test_the_ci_job_is_chromium_only_with_least_privilege() -> None:
    assert JOB["name"] == "Panel in a real browser (Chromium)"
    assert JOB["runs-on"] == "ubuntu-24.04"
    assert JOB["timeout-minutes"] == 15
    assert JOB["permissions"] == {"contents": "read"}
    runs = "\n".join(step.get("run", "") for step in STEPS)
    assert "uv sync --frozen --group panel" in runs
    assert "playwright install --only-shell chromium" in runs
    assert "make panel-check PANEL_BROWSERS=chromium" in runs
    for other in ("firefox", "webkit"):
        assert other not in JOB_TEXT.lower(), other


def test_the_ci_job_checks_out_without_the_token_and_pins_uv() -> None:
    quality = {step["uses"] for step in CI["jobs"]["quality"]["steps"] if "uses" in step}
    (checkout,) = [step for step in STEPS if step.get("uses", "").startswith("actions/checkout@")]
    assert checkout["uses"] in quality  # the same pinned SHA as the other jobs
    assert checkout["with"]["persist-credentials"] is False
    (setup,) = [step for step in STEPS if step.get("uses", "").startswith("astral-sh/setup-uv@")]
    assert setup["uses"] in quality
    assert setup["with"]["version"] == "${{ env.UV_VERSION }}"


def test_the_ci_job_reads_the_playwright_version_from_the_installed_package() -> None:
    (version,) = [step for step in STEPS if step.get("id") == "playwright"]
    assert 'version("playwright")' in version["run"]
    assert "GITHUB_OUTPUT" in version["run"]


def test_the_ci_job_caches_the_headless_shell_per_playwright_version() -> None:
    (cache,) = [step for step in STEPS if step.get("uses", "").startswith("actions/cache@")]
    assert re.fullmatch(r"actions/cache@[0-9a-f]{40}", cache["uses"])
    assert cache["with"]["path"] == "~/.cache/ms-playwright"
    assert cache["with"]["key"] == (
        "ms-playwright-${{ runner.os }}-${{ runner.arch }}-"
        "${{ steps.playwright.outputs.version }}-chromium-shell"
    )
    assert "restore-keys" not in cache["with"]
    (install,) = [s for s in STEPS if "playwright install --only-shell" in s.get("run", "")]
    assert install["if"] == f"steps.{cache['id']}.outputs.cache-hit != 'true'"
    names = [step.get("id") for step in STEPS]
    assert names.index("playwright") < names.index(cache["id"])


def test_the_system_libraries_step_fails_without_the_binary_and_survives_pipefail() -> None:
    (deps,) = [s for s in STEPS if "playwright install-deps" in s.get("run", "")]
    script = deps["run"]
    assert 'test -n "$shell"' in script  # no headless shell found: the step fails
    assert "grep -q" not in script  # -q exits early and ldd gets SIGPIPE under pipefail
    assert 'grep "not found" >/dev/null' in script


def test_the_cache_key_follows_the_lock() -> None:
    # The key reads the installed version, which uv.lock decides: the lock and the pin agree.
    assert f"playwright=={_locked('playwright')}" in PYPROJECT["dependency-groups"]["panel"]


def test_the_ci_job_uses_no_secret_and_no_expression_inside_a_script() -> None:
    assert "secrets." not in JOB_TEXT
    for step in STEPS:
        assert "${{" not in step.get("run", ""), step.get("name")


def test_the_ci_job_uploads_only_screenshots_and_redacted_logs_on_failure() -> None:
    (upload,) = [s for s in STEPS if s.get("uses", "").startswith("actions/upload-artifact@")]
    assert upload["if"] == "failure()"
    paths = upload["with"]["path"].split()
    assert paths == ["test-results/panel-browser/*.png", "test-results/panel-browser/*.log"]
    for word in ("trace", "video", ".har", "har:"):
        assert word not in JOB_TEXT.lower(), word
