"""NSS: python-stdnum has no NSS module, so it is tested with documented vectors.

No official TGSS source publishes the algorithm; vectors derived from the published algorithm.
The vectors, their arithmetic and their sources live in tests/data/nss_vectors.md.
"""

import re
from pathlib import Path

import pytest

from antifaz.detect.validators import nss

VECTORS_FILE = Path(__file__).parents[2] / "data" / "nss_vectors.md"
_ROW = re.compile(r"^\|\s*(?P<nss>[0-9 /]+?)\s*\|\s*(?P<expected>valid|invalid)\s*\|")


def _load_vectors() -> list[tuple[str, bool]]:
    vectors = []
    for line in VECTORS_FILE.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line)
        if match:
            vectors.append((match["nss"], match["expected"] == "valid"))
    return vectors


VECTORS = _load_vectors()


def test_the_vectors_file_has_valid_and_invalid_cases_for_both_branches() -> None:
    values = dict(VECTORS)
    assert values["28 12345678 40"] is True  # number >= 10^7
    assert values["28 01234567 42"] is True  # number < 10^7
    assert values["28 01234567 85"] is False
    assert len(VECTORS) >= 6


@pytest.mark.parametrize(("value", "expected"), VECTORS, ids=[v for v, _ in VECTORS])
def test_nss_matches_the_documented_vectors(value: str, expected: bool) -> None:
    assert nss.is_valid(value) is expected


@pytest.mark.parametrize(
    "value",
    [
        "28 12345678 4",  # 11 digits
        "28 12345678 400",  # 13 digits
        "28 1234567A 40",
        "",
    ],
)
def test_nss_with_wrong_length_or_characters_is_not_valid(value: str) -> None:
    assert nss.is_valid(value) is False
