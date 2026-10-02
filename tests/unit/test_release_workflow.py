"""Static checks of .github/workflows/release.yml, the workflow that publishes (issue 7c).

They run in `make check`. If one fails, the separation between scanning (ci.yml) and
publishing (release.yml) of ANTIFAZ.md section 5.2 was weakened.
"""

import re
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
RELEASE_TEXT = (REPO / ".github/workflows/release.yml").read_text(encoding="utf-8")
RELEASE: dict[Any, Any] = yaml.safe_load(RELEASE_TEXT)
# PyYAML (YAML 1.1) reads the `on:` key as the boolean True.
TRIGGER: dict[str, Any] = RELEASE.get("on", RELEASE.get(True))
(JOB_NAME,) = RELEASE["jobs"]
JOB: dict[str, Any] = RELEASE["jobs"][JOB_NAME]
STEPS: list[dict[str, Any]] = JOB["steps"]

# Only first-party GitHub and Docker actions may hold the permission to publish.
ALLOWED_ACTIONS = {
    "actions/checkout",
    "docker/setup-buildx-action",
    "docker/login-action",
    "docker/metadata-action",
    "docker/build-push-action",
    "actions/attest-build-provenance",
}


def _uses() -> list[str]:
    return [str(s["uses"]) for s in STEPS if "uses" in s]


def _step(action: str) -> dict[str, Any]:
    (step,) = [s for s in STEPS if str(s.get("uses", "")).startswith(f"{action}@")]
    return step


def test_only_a_version_tag_starts_it() -> None:
    assert TRIGGER == {"push": {"tags": ["v*.*.*"]}}


def test_no_permissions_by_default_and_the_minimum_in_the_job() -> None:
    assert RELEASE["permissions"] == {}
    assert JOB["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
    }


def test_one_job_in_the_protected_release_environment() -> None:
    assert JOB_NAME == "publish-image"
    assert JOB["environment"] == "release"


def test_releases_never_cancel_each_other() -> None:
    assert RELEASE["concurrency"]["cancel-in-progress"] is False


def test_every_action_is_pinned_by_full_sha() -> None:
    uses = _uses()
    assert uses
    for use in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", use), use


def test_only_first_party_actions_and_no_scanners() -> None:
    assert {u.split("@")[0] for u in _uses()} == ALLOWED_ACTIONS
    # Third-party tools (scanners, installers) run in ci.yml, never where the token can publish.
    # Checked on the parsed workflow, so the comments that name them do not count.
    parsed = yaml.safe_dump(RELEASE).lower()
    for tool in ("anchore", "grype", "syft", "trivy", "gitleaks", "zizmor", "uvx", "pip install"):
        assert tool not in parsed, tool


def test_checkout_does_not_keep_the_token() -> None:
    assert _step("actions/checkout")["with"]["persist-credentials"] is False


def test_the_tag_must_match_the_version_before_anything_is_built() -> None:
    checks = [s for s in STEPS if "run" in s and "pyproject.toml" in s["run"]]
    assert len(checks) == 1
    (check,) = checks
    assert "GITHUB_REF_NAME" in check["run"]
    assert "exit 1" in check["run"]
    # No ${{ }} expression inside the script (template injection).
    assert "${{" not in check["run"]
    assert STEPS.index(check) < STEPS.index(_step("docker/build-push-action"))


def test_no_latest_tag_while_in_beta() -> None:
    meta = _step("docker/metadata-action")["with"]
    assert "latest=false" in meta["flavor"]
    assert "latest" not in meta["tags"]
    assert meta["images"] == "${{ env.IMAGE }}"
    assert RELEASE["env"]["IMAGE"] == "ghcr.io/miquel-moreno/antifaz"


def test_the_image_is_built_amd64_only_without_cache_and_with_sbom_and_provenance() -> None:
    build = _step("docker/build-push-action")["with"]
    assert build["platforms"] == "linux/amd64"
    assert build["push"] is True
    assert build["no-cache"] is True
    assert "cache-from" not in build and "cache-to" not in build
    assert build["provenance"] == "mode=max"
    assert build["sbom"] is True


def test_the_provenance_attestation_is_pushed_with_the_image() -> None:
    attest = _step("actions/attest-build-provenance")["with"]
    assert attest["push-to-registry"] is True
    assert attest["subject-digest"] == "${{ steps.build.outputs.digest }}"
    assert STEPS.index(_step("actions/attest-build-provenance")) > STEPS.index(
        _step("docker/build-push-action")
    )


def test_the_registry_login_uses_only_the_job_token() -> None:
    login = _step("docker/login-action")["with"]
    assert login["registry"] == "ghcr.io"
    assert login["password"] == "${{ secrets.GITHUB_TOKEN }}"
    # No other stored secret anywhere in the workflow.
    assert set(re.findall(r"secrets\.(\w+)", RELEASE_TEXT)) == {"GITHUB_TOKEN"}
