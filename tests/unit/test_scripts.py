"""The CI helper scripts: license rule and privacy-coverage rule."""

import subprocess
from importlib import metadata
from pathlib import Path

import pytest
from scripts import check_licenses
from scripts.check_coverage import check, main
from scripts.check_licenses import Verdict, classify, documented_packages


@pytest.mark.parametrize(
    ("texts", "verdict"),
    [
        (["MIT"], Verdict.OK),
        (["Apache-2.0"], Verdict.OK),
        (["", "License :: OSI Approved :: BSD License"], Verdict.OK),
        (["PSF-2.0"], Verdict.OK),
        (["LGPL-2.1-or-later"], Verdict.WEAK_COPYLEFT),
        (["MPL-2.0"], Verdict.WEAK_COPYLEFT),
        (
            ["", "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)"],
            Verdict.WEAK_COPYLEFT,
        ),
        (["GPL-3.0-only"], Verdict.FORBIDDEN),
        (["AGPL-3.0-or-later"], Verdict.FORBIDDEN),
        (
            ["", "License :: OSI Approved :: GNU General Public License v3 (GPLv3)"],
            Verdict.FORBIDDEN,
        ),
        (["CC-BY-NC-ND-4.0"], Verdict.FORBIDDEN),
        (["SSPL-1.0"], Verdict.FORBIDDEN),
        (["MIT OR GPL-2.0"], Verdict.FORBIDDEN),  # any strong copyleft option is flagged
        ([""], Verdict.UNKNOWN),
        (["UNKNOWN"], Verdict.UNKNOWN),
        (["Copyright (c) someone"], Verdict.UNKNOWN),
    ],
)
def test_license_classification(texts: list[str], verdict: Verdict) -> None:
    assert classify(texts) is verdict


def coverage_report(files: dict[str, tuple[int, int]]) -> dict[str, object]:
    return {
        "files": {
            path: {"summary": {"covered_lines": covered, "num_statements": statements}}
            for path, (covered, statements) in files.items()
        }
    }


def test_privacy_pieces_need_ninety_percent() -> None:
    report = coverage_report(
        {
            "src/antifaz/detect/validators/dni.py": (19, 20),  # 95 % -> ok
            "src\\antifaz\\mask\\core.py": (8, 10),  # 80 % -> too low (Windows path)
            "src/antifaz/cli/__init__.py": (1, 10),  # not a privacy piece
        }
    )

    assert check(report) == ["mask: 80.0 % < 90 %"]


def test_paths_outside_the_package_do_not_count() -> None:
    assert check(coverage_report({"detect/other_project.py": (0, 10)})) == []


def test_a_missing_coverage_file_is_a_clear_failure(tmp_path: Path) -> None:
    assert main(tmp_path / "coverage.json") == 1


def test_pieces_without_code_are_skipped() -> None:
    assert check(coverage_report({"src/antifaz/guard/__init__.py": (0, 0)})) == []


def test_only_table_rows_count_as_documented() -> None:
    doc = (
        "certifi is mentioned here in passing.\n\n"
        "| Paquete | Licencia |\n|---|---|\n| python-stdnum | LGPL |\n"
    )

    assert documented_packages(doc) == {"python-stdnum"}


def test_validators_need_ninety_five_percent() -> None:
    report = coverage_report({"src/antifaz/detect/validators/dni.py": (93, 100)})

    assert check(report) == ["detect/validators: 93.0 % < 95 %"]


def test_license_check_reads_the_optional_extras_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `ner` extra ships with Antifaz too: its locked packages are checked like the rest."""
    seen: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="fastapi==1.0\ntorch==2.0 ; x\n")

    monkeypatch.setattr(check_licenses.subprocess, "run", fake_run)

    assert check_licenses.runtime_dependencies() == [("fastapi", False), ("torch", True)]
    assert "--all-extras" in seen[0]


def test_a_package_of_an_extra_that_is_not_installed_uses_its_recorded_license(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CI never installs torch: the licenses of the extra, read by hand from their metadata,
    stand in for the missing metadata."""

    def missing(name: str) -> object:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(check_licenses.metadata, "metadata", missing)

    assert check_licenses.license_texts("torch") == [check_licenses.RECORDED_EXTRA["torch"]]
    with pytest.raises(metadata.PackageNotFoundError):
        check_licenses.license_texts("not-a-recorded-package")


def test_every_recorded_license_is_allowed_and_weak_copyleft_is_documented() -> None:
    documented = documented_packages(check_licenses.LICENSES_DOC.read_text(encoding="utf-8"))
    for name, text in check_licenses.RECORDED_EXTRA.items():
        verdict = classify([text])
        assert verdict in (Verdict.OK, Verdict.WEAK_COPYLEFT), name
        if verdict is Verdict.WEAK_COPYLEFT:
            assert name in documented, name


def test_every_package_only_the_extras_bring_has_a_recorded_license() -> None:
    """Otherwise CI (without the extra) could not check it and the license job would fail."""
    with_extras = {name for name, _ in check_licenses.runtime_dependencies()}
    without = {name for name, _ in check_licenses.runtime_dependencies(extras=False)}

    assert with_extras - without <= set(check_licenses.RECORDED_EXTRA)


def test_recorded_licenses_match_the_installed_metadata() -> None:
    """Where the extra is installed, what was recorded by hand must say the same."""
    checked = 0
    for name, text in check_licenses.RECORDED_EXTRA.items():
        try:
            meta = metadata.metadata(name)
        except metadata.PackageNotFoundError:
            continue
        checked += 1
        assert classify(check_licenses.metadata_texts(meta)) is classify([text]), name
    if not checked:
        pytest.skip("the ner extra is not installed")
