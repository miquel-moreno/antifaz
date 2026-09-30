"""The CI helper scripts: license rule and privacy-coverage rule."""

from pathlib import Path

import pytest
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
