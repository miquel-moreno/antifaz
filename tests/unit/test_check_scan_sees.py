"""scripts/check_scan_sees.py: the CI fails if the scanners go blind on the image (issue 54).

On a distroless image the system packages are only visible through the records in
/var/lib/dpkg/status.d. If those were lost (or a copied library had no record), Syft would list
fewer packages and Grype would report fewer vulnerabilities, and the gate would pass for the
wrong reason. So the CI checks that the SBOM still lists a known Debian package and the CPython
binary, and that the Grype report still holds a known vulnerability of that package. Small JSON
documents with the shape of Syft's CycloneDX and Grype's JSON output.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from scripts.check_scan_sees import check, main

ZLIB = {
    "type": "library",
    "name": "zlib1g",
    "version": "1:1.3.dfsg+really1.3.1-1+b1",
    "purl": "pkg:deb/debian/zlib1g@1%3A1.3.dfsg%2Breally1.3.1-1%2Bb1?arch=amd64&distro=debian-13",
}
PYTHON = {
    "type": "application",
    "name": "python",
    "version": "3.12.15",
    "purl": "pkg:generic/python@3.12.15",
}
PYPI_ZLIB = {"type": "library", "name": "zlib1g", "purl": "pkg:pypi/zlib1g@1.0"}
CVE = "CVE-2026-85091"
ARGS = {"package": "zlib1g", "vulnerability": CVE, "python": "3.12"}


def _sbom(*components: dict[str, str]) -> dict[str, Any]:
    return {"bomFormat": "CycloneDX", "components": list(components)}


def _report(*matches: tuple[str, str]) -> dict[str, Any]:
    return {
        "matches": [
            {"vulnerability": {"id": vuln, "severity": "High"}, "artifact": {"name": name}}
            for vuln, name in matches
        ]
    }


GOOD_SBOM = _sbom(ZLIB, PYTHON)
GOOD_REPORT = _report((CVE, "zlib1g"), ("CVE-2026-00001", "libc6"))


def test_a_scan_that_sees_everything_passes() -> None:
    assert check(GOOD_SBOM, GOOD_REPORT, **ARGS) == []


@pytest.mark.parametrize(
    ("sbom", "report", "problem"),
    [
        (_sbom(PYTHON), GOOD_REPORT, "the SBOM does not list the Debian package zlib1g"),
        (_sbom(PYPI_ZLIB, PYTHON), GOOD_REPORT, "the SBOM does not list the Debian package zlib1g"),
        (_sbom(ZLIB), GOOD_REPORT, "the SBOM does not list CPython 3.12.x"),
        (
            _sbom(ZLIB, PYTHON | {"version": "3.13.1", "purl": "pkg:generic/python@3.13.1"}),
            GOOD_REPORT,
            "the SBOM does not list CPython 3.12.x",
        ),
        (GOOD_SBOM, _report(), f"the Grype report does not hold {CVE} in zlib1g"),
        (GOOD_SBOM, _report((CVE, "libc6")), f"the Grype report does not hold {CVE} in zlib1g"),
    ],
    ids=["no-deb", "pypi-not-deb", "no-python", "other-python", "no-cve", "cve-elsewhere"],
)
def test_a_blind_scan_fails_with_a_clear_reason(
    sbom: dict[str, Any], report: dict[str, Any], problem: str
) -> None:
    assert check(sbom, report, **ARGS) == [problem]


@pytest.mark.parametrize(
    "match",
    [
        {"vulnerability": "x", "artifact": {"name": "zlib1g"}},
        {"vulnerability": {"id": CVE}, "artifact": "zlib1g"},
        {"vulnerability": None, "artifact": None},
        "not a match",
    ],
    ids=["vulnerability-string", "artifact-string", "nulls", "match-string"],
)
def test_a_malformed_match_is_not_seen_and_raises_nothing(match: object) -> None:
    report = {"matches": [match]}
    assert check(GOOD_SBOM, report, **ARGS) == [f"the Grype report does not hold {CVE} in zlib1g"]


def test_an_empty_scan_names_every_problem() -> None:
    assert len(check({}, {}, **ARGS)) == 3


def _write(folder: Path, sbom: dict[str, Any], report: dict[str, Any]) -> list[str]:
    (folder / "sbom.json").write_text(json.dumps(sbom), encoding="utf-8")
    (folder / "grype.json").write_text(json.dumps(report), encoding="utf-8")
    return [
        "--sbom", str(folder / "sbom.json"), "--grype", str(folder / "grype.json"),
        "--package", "zlib1g", "--vulnerability", CVE, "--python", "3.12",
    ]  # fmt: skip


def test_main_returns_zero_when_the_scan_sees_everything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(_write(tmp_path, GOOD_SBOM, GOOD_REPORT)) == 0
    assert "the scanners see" in capsys.readouterr().out


def test_main_fails_and_says_why_when_the_scan_is_blind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(_write(tmp_path, _sbom(PYTHON), GOOD_REPORT)) == 1
    assert "zlib1g" in capsys.readouterr().out


def test_main_fails_clearly_on_a_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _write(tmp_path, GOOD_SBOM, GOOD_REPORT)
    (tmp_path / "grype.json").unlink()
    assert main(args) == 1
    assert "grype.json" in capsys.readouterr().out


def test_main_fails_clearly_on_a_file_that_is_not_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _write(tmp_path, GOOD_SBOM, GOOD_REPORT)
    (tmp_path / "sbom.json").write_text("not json", encoding="utf-8")
    assert main(args) == 1
    assert "sbom.json" in capsys.readouterr().out
