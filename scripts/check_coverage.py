"""Require >= 90 % line coverage in the privacy pieces and 95 % in the validators.

ANTIFAZ spec 5.2 and issue 2.

Reads coverage.json (pytest --cov-report=json). Pieces without code yet are skipped.

    uv run python -m scripts.check_coverage
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# piece (path under src/antifaz/) -> minimum line coverage in %
MINIMUMS = {
    "detect": 90.0,
    "detect/validators": 95.0,
    "policy": 90.0,
    "vault": 90.0,
    "mask": 90.0,
    "guard": 90.0,
    "restore": 90.0,
    "providers": 90.0,
    "api": 90.0,
    "web": 90.0,
}


def piece_coverage(report: dict[str, object], piece: str) -> tuple[int, int]:
    """(covered lines, statements) of every file under src/antifaz/<piece>/."""
    covered = statements = 0
    files = report.get("files", {})
    assert isinstance(files, dict)
    for path, data in files.items():
        # coverage.json keeps the OS separator; on Linux, Path() would not convert "\\".
        posix = path.replace("\\", "/")
        if "src/antifaz/" in posix and posix.split("src/antifaz/", 1)[1].startswith(f"{piece}/"):
            summary = data["summary"]
            covered += int(summary["covered_lines"])
            statements += int(summary["num_statements"])
    return covered, statements


def check(report: dict[str, object]) -> list[str]:
    problems = []
    for piece, minimum in MINIMUMS.items():
        covered, statements = piece_coverage(report, piece)
        if statements == 0:
            print(f"{piece:<18} no code yet")
            continue
        percent = 100 * covered / statements
        print(f"{piece:<18} {percent:5.1f} % ({covered}/{statements} lines, min {minimum:.0f} %)")
        if percent < minimum:
            problems.append(f"{piece}: {percent:.1f} % < {minimum:.0f} %")
    return problems


def main(path: Path = ROOT / "coverage.json") -> int:
    if not path.exists():
        print(f"{path.name} not found: run the tests with --cov-report=json first", file=sys.stderr)
        return 1
    problems = check(json.loads(path.read_text(encoding="utf-8")))
    if problems:
        print("Privacy coverage too low:\n- " + "\n- ".join(problems), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
