"""The CI helper scripts: license rule and privacy-coverage rule."""

import pytest
from scripts.check_coverage import check
from scripts.check_licenses import Verdict, classify


@pytest.mark.parametrize(
    ("texts", "verdict"),
    [
        (["MIT"], Verdict.OK),
        (["Apache-2.0"], Verdict.OK),
        (["", "License :: OSI Approved :: BSD License"], Verdict.OK),
        (["PSF-2.0"], Verdict.OK),
        (["LGPL-2.1-or-later"], Verdict.WEAK_COPYLEFT),
        (["MPL-2.0"], Verdict.WEAK_COPYLEFT),
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
            "src/antifaz/detect/validators/dni.py": (18, 20),  # 90 % -> ok
            "src\\antifaz\\mask\\core.py": (8, 10),  # 80 % -> too low (Windows path)
            "src/antifaz/api/app.py": (1, 10),  # not a privacy piece
        }
    )

    assert check(report) == ["mask: 80.0 % < 90 %"]


def test_pieces_without_code_are_skipped() -> None:
    assert check(coverage_report({"src/antifaz/guard/__init__.py": (0, 0)})) == []
