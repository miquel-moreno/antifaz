"""Every validator returns a plain bool for any input and never raises."""

from collections.abc import Callable

import pytest

from antifaz.detect.validators import ccc, cif, dni, eu, iban, luhn, nie, nif_klm, nss
from tests.unit.detect._unicode_digits import arabic_indic, full_width, persian

VALIDATORS: dict[str, Callable[[str], bool]] = {
    "dni": dni.is_valid,
    "nie": nie.is_valid,
    "nif_klm": nif_klm.is_valid,
    "cif": cif.is_valid,
    "nss": nss.is_valid,
    "ccc": ccc.is_valid,
    "iban": iban.is_valid,
    "luhn": luhn.is_valid,
    "it_codice_fiscale": eu.it_codice_fiscale_is_valid,
    "eu_vat": eu.eu_vat_is_valid,
}

GARBAGE = [
    "",
    " ",
    "-",
    "./-",
    "Z",
    "ñññññññññ",
    "\x00\x00\x00",
    "😀" * 9,
    "[[ES_DNI_1]]",
    "12345678Z" * 200,
    "0" * 4999 + "A",
    full_width("12345678Z"),
    arabic_indic("12345678Z"),
    "X" + full_width("2482300") + "W",
    "ES07 " + full_width("0012") + " 0345 03 0000067890",
    arabic_indic("4111111111111111"),
]


@pytest.mark.parametrize("value", GARBAGE, ids=lambda v: repr(v[:12]))
@pytest.mark.parametrize("name", list(VALIDATORS))
def test_validators_return_false_for_garbage_without_raising(name: str, value: str) -> None:
    result = VALIDATORS[name](value)
    assert result is False


@pytest.mark.parametrize(
    "value",
    [
        full_width("12345678Z"),
        full_width("12345678") + "-Z",
        arabic_indic("12345678Z"),
        persian("12345678Z"),
    ],
)
def test_dni_written_with_unicode_digits_is_not_valid(value: str) -> None:
    # "١٢٣".isdigit() is True and int("١٢٣") == 123: only ASCII digits may count.
    assert dni.is_valid(value) is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("dni", "12345678Z"),
        ("nie", "X2482300W"),
        ("nif_klm", "K1234567L"),
        ("cif", "P1234567D"),
        ("nss", "28 12345678 40"),
        ("ccc", "00120345030000067890"),
        ("iban", "ES0700120345030000067890"),
        ("luhn", "4111111111111111"),
        ("it_codice_fiscale", "RCCMNL83S18D969H"),
        ("eu_vat", "FR61954506077"),
    ],
)
def test_validators_return_exactly_true_for_a_valid_value(name: str, value: str) -> None:
    assert VALIDATORS[name](value) is True
