import pytest

from antifaz.detect.validators._ascii import compact, is_ascii_digits
from tests.unit.detect._unicode_digits import arabic_indic, full_width, persian


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12345678Z", "12345678Z"),
        ("12345678-Z", "12345678Z"),
        ("12.345.678-Z", "12345678Z"),
        ("12345678 z", "12345678Z"),
        ("28/12345678/40", "281234567840"),
        ("es07 0012 0345 03 0000067890", "ES0700120345030000067890"),
        ("", ""),
    ],
)
def test_compact_removes_separators_and_uppercases(raw: str, expected: str) -> None:
    assert compact(raw) == expected


def test_compact_keeps_other_characters_so_validators_can_reject_them() -> None:
    assert compact("12345678_Z") == "12345678_Z"


@pytest.mark.parametrize("value", ["0", "0123456789", "12345678"])
def test_ascii_digits_are_accepted(value: str) -> None:
    assert is_ascii_digits(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        "1234567a",
        full_width("12345678"),
        arabic_indic("12345678"),
        persian("12345678"),
        "²³",  # superscripts: str.isdigit() is True for them
        "12 34",
        "-1",
    ],
)
def test_non_ascii_digits_and_other_characters_are_rejected(value: str) -> None:
    assert is_ascii_digits(value) is False
