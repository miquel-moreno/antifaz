"""Egress guard: blocks any hidden value found in the final bytes (spec 3.2, piece 6)."""

import json

import pytest

from antifaz import EgressBlocked, mask
from antifaz.detect.types import EntityType
from antifaz.guard import check
from antifaz.vault import Vault

DNI = "12345678Z"  # synthetic, valid check letter
EMAIL = "ana@example.com"


def vault_with(*pairs: tuple[EntityType, str]) -> Vault:
    vault = Vault()
    for entity, value in pairs:
        vault._add(entity, value)
    return vault


def test_empty_vault_is_a_no_op() -> None:
    check("Mi DNI es 12345678Z", Vault())


def test_masked_output_passes() -> None:
    result = mask(f"Mi DNI es {DNI} y mi correo {EMAIL}")
    check(json.dumps({"messages": [{"content": result.text}]}), result.vault)


@pytest.mark.parametrize(
    "payload",
    [
        "Mi DNI es 12345678Z",
        "mi dni es 12345678z",
        "DNI: 12.345.678-Z.",
        "DNI 12 345 678 Z",
        "DNI 12345678/Z",
        b"DNI 12345678Z",
    ],
)
def test_value_in_clear_or_formatted_is_blocked(payload: str | bytes) -> None:
    with pytest.raises(EgressBlocked):
        check(payload, vault_with((EntityType.ES_DNI, DNI)))


def test_json_unicode_escapes_are_decoded() -> None:
    escaped = "".join(f"\\u{ord(c):04x}" for c in DNI)
    payload = '{"content": "DNI ' + escaped + '"}'
    assert DNI not in payload
    with pytest.raises(EgressBlocked):
        check(payload, vault_with((EntityType.ES_DNI, DNI)))


def test_json_keys_are_checked() -> None:
    payload = json.dumps({"x": [{"ana@example.com": 1}]}, ensure_ascii=True)
    with pytest.raises(EgressBlocked):
        check(payload.replace("a", "\\u0061", 1), vault_with((EntityType.EMAIL, EMAIL)))


def test_email_nfc_and_case_are_normalised() -> None:
    value = "josé@example.com"
    with pytest.raises(EgressBlocked):
        check("escribe a JOSÉ@EXAMPLE.COM", vault_with((EntityType.EMAIL, value)))


@pytest.mark.parametrize("payload", ["X12345678ZY", "112345678Z", "12345678ZZ", "a12.345.678-Z"])
def test_value_inside_a_longer_alphanumeric_run_is_not_matched(payload: str) -> None:
    check(payload, vault_with((EntityType.ES_DNI, DNI)))


def test_word_boundary_for_short_values() -> None:
    vault = vault_with((EntityType.ADDRESS, "Ana"))
    check("la semana que viene", vault)
    with pytest.raises(EgressBlocked):
        check("hola, Ana.", vault)


def test_email_is_not_compacted() -> None:
    # No compact form for emails: dots and hyphens are part of the value.
    check("anaexamplecom", vault_with((EntityType.EMAIL, "ana@example.com")))


def test_invalid_utf8_is_blocked() -> None:
    with pytest.raises(EgressBlocked):
        check(b"\xff\xfe DNI", vault_with((EntityType.ES_DNI, DNI)))


def test_error_never_carries_the_value() -> None:
    with pytest.raises(EgressBlocked) as info:
        check(f"DNI {DNI}", vault_with((EntityType.ES_DNI, DNI)))
    error = info.value
    assert DNI not in str(error)
    assert DNI not in repr(error)
    assert DNI.lower() not in str(error.args).lower()
    assert error.__context__ is None
    assert error.__cause__ is None


def test_decode_error_is_not_the_context() -> None:
    with pytest.raises(EgressBlocked) as info:
        check(b"\xff" + DNI.encode(), vault_with((EntityType.ES_DNI, DNI)))
    assert info.value.__context__ is None


def test_too_deep_json_is_blocked() -> None:
    payload = "[" * 100_000 + "]" * 100_000
    with pytest.raises(EgressBlocked):
        check(payload, vault_with((EntityType.ES_DNI, DNI)))
