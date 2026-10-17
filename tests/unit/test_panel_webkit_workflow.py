"""The on-demand workflow that runs the panel checks in WebKit on Linux (PR 11c-2a).

It runs only by hand (workflow_dispatch) or on a pull request that changes the workflow itself:
WebKit on Linux needs about a dozen system packages and its own browser, too much for every
push. No cache, no secret, read-only token, every action pinned by full SHA, WebKit only.
"""

import re
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
PATH = ".github/workflows/panel-webkit.yml"
WORKFLOW: dict[Any, Any] = yaml.safe_load((REPO / PATH).read_text(encoding="utf-8"))
CI: dict[str, Any] = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
# PyYAML reads the bare key `on` as True.
TRIGGERS: dict[str, Any] = WORKFLOW.get("on", WORKFLOW.get(True, {}))
(JOB,) = WORKFLOW["jobs"].values()
STEPS: list[dict[str, Any]] = JOB["steps"]
TEXT = yaml.safe_dump(WORKFLOW)
RUNS = "\n".join(step.get("run", "") for step in STEPS)


def test_it_runs_only_by_hand_or_when_it_changes() -> None:
    assert set(TRIGGERS) == {"workflow_dispatch", "pull_request"}
    assert TRIGGERS["pull_request"] == {"paths": [PATH]}


def test_least_privilege() -> None:
    assert WORKFLOW["permissions"] == {}
    assert JOB["permissions"] == {"contents": "read"}
    assert JOB["name"] == "Panel in WebKit on Linux"
    assert JOB["runs-on"] == "ubuntu-24.04"
    assert JOB["timeout-minutes"] == 20


def test_every_action_is_pinned_like_ci() -> None:
    ci_uses = {s["uses"] for job in CI["jobs"].values() for s in job["steps"] if "uses" in s}
    for step in STEPS:
        if "uses" in step:
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
            assert step["uses"] in ci_uses, step["uses"]
    (checkout,) = [s for s in STEPS if s.get("uses", "").startswith("actions/checkout@")]
    assert checkout["with"]["persist-credentials"] is False
    (setup,) = [s for s in STEPS if s.get("uses", "").startswith("astral-sh/setup-uv@")]
    assert setup["with"]["version"] == "${{ env.UV_VERSION }}"
    assert WORKFLOW["env"]["UV_VERSION"] == CI["env"]["UV_VERSION"]


def test_no_cache_no_secret_no_expression_in_scripts() -> None:
    assert "actions/cache" not in TEXT
    assert "enable-cache: true" not in TEXT
    assert "secrets." not in TEXT
    for step in STEPS:
        assert "${{" not in step.get("run", ""), step.get("name")


def test_webkit_only_with_its_system_packages_and_sizes_in_the_log() -> None:
    assert "uv sync --frozen --group panel" in RUNS
    assert "uv run --no-sync playwright install --with-deps webkit" in RUNS
    assert "du -sh ~/.cache/ms-playwright" in RUNS
    assert "make panel-check PANEL_BROWSERS=webkit" in RUNS
    for other in ("chromium", "firefox"):
        assert other not in TEXT.lower(), other


def test_it_uploads_only_screenshots_and_redacted_logs_on_failure() -> None:
    (upload,) = [s for s in STEPS if s.get("uses", "").startswith("actions/upload-artifact@")]
    assert upload["if"] == "failure()"
    paths = upload["with"]["path"].split()
    assert paths == ["test-results/panel-browser/*.png", "test-results/panel-browser/*.log"]
    for word in ("trace", "video", ".har", "har:"):
        assert word not in TEXT.lower(), word
