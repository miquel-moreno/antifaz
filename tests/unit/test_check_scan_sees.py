"""scripts/check_scan_sees.py: the CI fails if the scanners go blind on the image (issue 54).

On a distroless image the system packages are visible only through the records in
/var/lib/dpkg/status.d. If those were lost (or a copied library had no record), Syft would list
fewer packages and Grype would report fewer vulnerabilities, and the gate would pass for the
wrong reason. Miquel's rule: the STRUCTURE blocks (Grype sees Debian 13 and matches deb
packages; the SBOM lists libc6, libssl3t64 and zlib1g as Debian packages and CPython 3.12.x),
the known CVE only WARNS (it may be fixed or reclassified one day). Small JSON documents with
the shape of Syft's CycloneDX and Grype's JSON output.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from scripts.check_scan_sees import check, check_report, check_sbom, main


def _deb(name: str) -> dict[str, str]:
    return {
        "type": "library",
        "name": name,
        "version": "1.0-1",
        "purl": f"pkg:deb/debian/{name}@1.0-1?arch=amd64&distro=debian-13",
    }


PYTHON = {
    "type": "application",
    "name": "python",
    "version": "3.12.15",
    "purl": "pkg:generic/python@3.12.15",
}
DEBS = ("libc6", "libssl3t64", "zlib1g")
CVE = "CVE-2026-85091"
ARGS: dict[str, Any] = {
    "distro": "debian",
    "distro_version": "13",
    "deb_packages": DEBS,
    "python": "3.12",
    "vulnerability": CVE,
    "package": "zlib1g",
}
SBOM_ARGS = {"deb_packages": DEBS, "python": "3.12"}
REPORT_ARGS = {"distro": "debian", "distro_version": "13"}


def _sbom(*components: dict[str, str]) -> dict[str, Any]:
    return {"bomFormat": "CycloneDX", "components": list(components)}


def _match(vuln: str, name: str, kind: str = "deb") -> dict[str, Any]:
    return {"vulnerability": {"id": vuln}, "artifact": {"name": name, "type": kind}}


def _report(*matches: Any, distro: Any = None) -> dict[str, Any]:
    if distro is None:
        distro = {"name": "debian", "version": "13.7"}
    return {"distro": distro, "matches": list(matches)}


GOOD_SBOM = _sbom(*(_deb(name) for name in DEBS), PYTHON)
GOOD_REPORT = _report(_match(CVE, "zlib1g"), _match("CVE-2025-1", "python", "binary"))


def test_a_scan_that_sees_everything_passes_without_warnings() -> None:
    assert check(GOOD_SBOM, GOOD_REPORT, **ARGS) == ([], [])


# --- Structure: each rule missing blocks ----------------------------------------------------


@pytest.mark.parametrize("missing", DEBS)
def test_a_missing_debian_package_in_the_sbom_blocks(missing: str) -> None:
    sbom = _sbom(*(_deb(name) for name in DEBS if name != missing), PYTHON)
    assert check_sbom(sbom, **SBOM_ARGS) == [f"the SBOM does not list the Debian package {missing}"]


def test_a_package_that_is_not_a_deb_does_not_count() -> None:
    pypi = {"type": "library", "name": "zlib1g", "purl": "pkg:pypi/zlib1g@1.0"}
    sbom = _sbom(_deb("libc6"), _deb("libssl3t64"), pypi, PYTHON)
    assert check_sbom(sbom, **SBOM_ARGS) == ["the SBOM does not list the Debian package zlib1g"]


@pytest.mark.parametrize(
    "python",
    [None, PYTHON | {"version": "3.13.1", "purl": "pkg:generic/python@3.13.1"}],
    ids=["no-python", "other-python"],
)
def test_cpython_3_12_missing_from_the_sbom_blocks(python: dict[str, str] | None) -> None:
    components = [_deb(name) for name in DEBS] + ([python] if python else [])
    assert check_sbom(_sbom(*components), **SBOM_ARGS) == ["the SBOM does not list CPython 3.12.x"]


@pytest.mark.parametrize(
    ("distro", "problem"),
    [
        (
            {"name": "ubuntu", "version": "24.04"},
            "Grype does not see Debian 13 (it sees ubuntu 24.04)",
        ),
        ({"name": "debian", "version": "12"}, "Grype does not see Debian 13 (it sees debian 12)"),
        ({}, "Grype does not see Debian 13 (it sees ? ?)"),
        ("debian", "Grype does not see Debian 13 (it sees ? ?)"),
    ],
    ids=["ubuntu", "debian-12", "empty", "not-an-object"],
)
def test_grype_not_seeing_debian_13_blocks(distro: Any, problem: str) -> None:
    report = _report(_match(CVE, "zlib1g"), distro=distro)
    assert check_report(report, **REPORT_ARGS) == [problem]


def test_grype_without_any_deb_match_blocks() -> None:
    report = _report(_match("CVE-2025-1", "python", "binary"))
    assert check_report(report, **REPORT_ARGS) == ["Grype matched no Debian (deb) package"]


@pytest.mark.parametrize(
    "match",
    [
        {"vulnerability": "x", "artifact": "zlib1g"},
        {"vulnerability": None, "artifact": None},
        "not a match",
    ],
    ids=["strings", "nulls", "match-string"],
)
def test_malformed_matches_count_as_nothing_and_raise_nothing(match: object) -> None:
    problems, warnings = check(GOOD_SBOM, _report(match), **ARGS)
    assert problems == ["Grype matched no Debian (deb) package"]
    assert len(warnings) == 1


def test_an_empty_scan_names_every_structural_problem() -> None:
    problems, _ = check({}, {}, **ARGS)
    assert len(problems) == 6  # three packages, CPython, the distro and the deb matches


# --- The CVE only warns ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "report",
    [
        _report(_match("CVE-2026-00001", "libc6")),
        _report(_match(CVE, "libc6"), _match("CVE-2026-00001", "zlib1g")),
    ],
    ids=["cve-gone", "cve-elsewhere"],
)
def test_the_missing_cve_is_only_a_warning(report: dict[str, Any]) -> None:
    problems, warnings = check(GOOD_SBOM, report, **ARGS)
    assert problems == []
    assert len(warnings) == 1
    assert CVE in warnings[0] and "zlib1g" in warnings[0]
    assert "fixed" in warnings[0] or "reclassified" in warnings[0]


# --- The command line -----------------------------------------------------------------------


def _write(folder: Path, sbom: Any, report: Any) -> list[str]:
    (folder / "sbom.json").write_text(json.dumps(sbom), encoding="utf-8")
    (folder / "grype.json").write_text(json.dumps(report), encoding="utf-8")
    return [
        "--sbom", str(folder / "sbom.json"), "--grype", str(folder / "grype.json"),
        "--distro", "debian", "--distro-version", "13",
        "--deb", "libc6", "--deb", "libssl3t64", "--deb", "zlib1g", "--python", "3.12",
        "--warn-vulnerability", CVE, "--warn-package", "zlib1g",
    ]  # fmt: skip


def test_main_returns_zero_when_the_scan_sees_everything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(_write(tmp_path, GOOD_SBOM, GOOD_REPORT)) == 0
    out = capsys.readouterr().out
    assert "the scanners see" in out and "::warning::" not in out


def test_main_passes_with_a_github_warning_when_only_the_cve_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _report(_match("CVE-2026-00001", "zlib1g"))
    assert main(_write(tmp_path, GOOD_SBOM, report)) == 0
    warning = [line for line in capsys.readouterr().out.splitlines() if line.startswith("::")]
    assert len(warning) == 1 and warning[0].startswith("::warning::") and CVE in warning[0]


def test_main_fails_and_says_why_when_the_structure_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sbom = _sbom(_deb("libc6"), _deb("libssl3t64"), PYTHON)
    assert main(_write(tmp_path, sbom, GOOD_REPORT)) == 1
    out = capsys.readouterr().out
    assert "::error::" in out and "zlib1g" in out


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
