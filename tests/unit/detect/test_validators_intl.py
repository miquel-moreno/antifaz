"""IBAN, Luhn and the EU identifiers delegated to python-stdnum. All values are synthetic or
the published examples named next to each one."""

import pytest

from antifaz.detect.validators import eu, iban, luhn

# --- IBAN ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "ES07 0012 0345 03 0000067890",  # the AEB CCC vector with its IBAN check digits
        "ES0700120345030000067890",
        "es07 0012 0345 03 0000067890",
        # The widely published German example IBAN (e.g. in Wikipedia's IBAN article);
        # python-stdnum's iban.is_valid also accepts it.
        "DE89370400440532013000",
        "DE89 3704 0044 0532 0130 00",
    ],
)
def test_iban_with_correct_mod_97_is_valid_in_lower_case_and_with_spaces(value: str) -> None:
    assert iban.is_valid(value) is True


@pytest.mark.parametrize(
    "value",
    [
        # Mod-97 check digits are right, but the CCC inside is wrong: for Spain the CCC must
        # also be valid (like stdnum.iban.is_valid with check_country=True).
        "ES6900120345990000067890",
        "ES08 0012 0345 03 0000067890",  # wrong IBAN check digits
        "DE88370400440532013000",  # wrong IBAN check digits
        "DE8937040044053201300",  # too short for Germany
        "XX89370400440532013000",  # unknown country
        "ES07",
        "",
    ],
)
def test_iban_with_wrong_check_digits_ccc_or_length_is_not_valid(value: str) -> None:
    assert iban.is_valid(value) is False


# --- Luhn ----------------------------------------------------------------------------------
# Public test card numbers published by payment providers for sandbox use.


@pytest.mark.parametrize(
    "value",
    ["4111111111111111", "4111 1111 1111 1111", "5555555555554444", "378282246310005"],
)
def test_public_test_card_numbers_pass_luhn(value: str) -> None:
    assert luhn.is_valid(value) is True


@pytest.mark.parametrize("value", ["4111111111111112", "5555555555554445", "", "4111x111"])
def test_numbers_with_a_changed_digit_or_letters_fail_luhn(value: str) -> None:
    assert luhn.is_valid(value) is False


# --- Italian codice fiscale ----------------------------------------------------------------
# Examples from python-stdnum's own stdnum.it.codicefiscale docstring.


@pytest.mark.parametrize("value", ["RCCMNL83S18D969H", "rccmnl83s18d969h", "CNTCHR83T41D969D"])
def test_italian_codice_fiscale_examples_from_stdnum_are_valid(value: str) -> None:
    assert eu.it_codice_fiscale_is_valid(value) is True


@pytest.mark.parametrize("value", ["RCCMNL83S18D969A", "RCCMNL83S18D969", "", "12345678Z"])
def test_codice_fiscale_with_wrong_control_or_length_is_not_valid(value: str) -> None:
    assert eu.it_codice_fiscale_is_valid(value) is False


# --- EU VAT --------------------------------------------------------------------------------
# Examples from python-stdnum's own stdnum.eu.vat docstring.


@pytest.mark.parametrize("value", ["FR 61 954 506 077", "FR61954506077", "BE697449992"])
def test_eu_vat_examples_from_stdnum_are_valid(value: str) -> None:
    assert eu.eu_vat_is_valid(value) is True


@pytest.mark.parametrize("value", ["FR 62 954 506 077", "XX61954506077", "FR", ""])
def test_eu_vat_with_wrong_check_or_unknown_country_is_not_valid(value: str) -> None:
    assert eu.eu_vat_is_valid(value) is False


@pytest.mark.parametrize("name", ["it_codice_fiscale_is_valid", "eu_vat_is_valid"])
def test_eu_validators_never_raise_even_if_stdnum_breaks(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from antifaz.detect.validators import eu

    def broken(value: str) -> bool:
        raise RuntimeError(f"stdnum failed on {value}")  # the value must not escape

    monkeypatch.setattr(eu.codicefiscale, "is_valid", broken)
    monkeypatch.setattr(eu.vat, "is_valid", broken)

    assert getattr(eu, name)("RCCMNL83S18D969H") is False
