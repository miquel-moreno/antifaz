"""Require >= 90 % line coverage in the privacy pieces (ANTIFAZ spec 5.2).

Reads coverage.json (pytest --cov-report=json). Pieces without code yet are skipped.

    uv run python -m scripts.check_coverage
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVACY_PIECES = ("detect", "vault", "mask", "guard", "restore")
MINIMUM = 90.0


def piece_coverage(report: dict[str, object], piece: str) -> tuple[int, int]:
    """(covered lines, statements) of every file under src/antifaz/<piece>/."""
    covered = statements = 0
    files = report.get("files", {})
    assert isinstance(files, dict)
    for path, data in files.items():
        if Path(path).as_posix().split("src/antifaz/", 1)[-1].startswith(f"{piece}/"):
            summary = data["summary"]
            covered += int(summary["covered_lines"])
            statements += int(summary["num_statements"])
    return covered, statements


def check(report: dict[str, object]) -> list[str]:
    problems = []
    for piece in PRIVACY_PIECES:
        covered, statements = piece_coverage(report, piece)
        if statements == 0:
            print(f"{piece:<8} no code yet")
            continue
        percent = 100 * covered / statements
        print(f"{piece:<8} {percent:5.1f} % ({covered}/{statements} lines)")
        if percent < MINIMUM:
            problems.append(f"{piece}: {percent:.1f} % < {MINIMUM:.0f} %")
    return problems


def main(path: Path = ROOT / "coverage.json") -> int:
    problems = check(json.loads(path.read_text(encoding="utf-8")))
    if problems:
        print("Privacy coverage too low:\n- " + "\n- ".join(problems), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
