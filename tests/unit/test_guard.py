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
    value = "jos\u00e9@example.com"
    with pytest.raises(EgressBlocked):
        check("escribe a JOSE\u0301@EXAMPLE.COM", vault_with((EntityType.EMAIL, value)))


@pytest.mark.parametrize(
    "payload",
    [
        "DNI12345678Z",
        "12345678Zabc",
        "X12345678ZY",
        "a12.345.678-Z",
        "DNI\t12\u00a0345\u200b678_Z",
        "\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff3a",  # fullwidth
        "12\u00ad345678\u200dZ",  # soft hyphen, zero-width joiner
        "12,345,678\nz",
    ],
)
def test_long_values_are_found_glued_or_obfuscated(payload: str) -> None:
    with pytest.raises(EgressBlocked):
        check(payload, vault_with((EntityType.ES_DNI, DNI)))


@pytest.mark.parametrize(
    ("entity", "value", "payload"),
    [
        (EntityType.IBAN, "ES9121000418450200051332", "IBANES9121000418450200051332"),
        (EntityType.EMAIL, EMAIL, "xana@example.com"),
        (EntityType.EMAIL, EMAIL, "anaexamplecom"),
    ],
)
def test_other_glued_values(entity: EntityType, value: str, payload: str) -> None:
    with pytest.raises(EgressBlocked):
        check(payload, vault_with((entity, value)))


def test_mask_then_check_catches_a_glued_copy() -> None:
    # The detector does not take a DNI glued to digits; the guard still sees the copy.
    result = mask("Mi DNI es 12345678Z y ref 012345678Z")
    with pytest.raises(EgressBlocked):
        check(json.dumps({"content": result.text}), result.vault)


def test_accents_and_dotted_i_are_removed() -> None:
    vault = vault_with((EntityType.ADDRESS, "\u0130brahim"))
    with pytest.raises(EgressBlocked):
        check("hola ibrahim", vault)


def test_short_values_collapse_whitespace() -> None:
    vault = vault_with((EntityType.ADDRESS, "C/ Sol 3"))
    with pytest.raises(EgressBlocked):
        check("en c/\u00a0 sol\t3.", vault)


def test_json_numbers_are_checked() -> None:
    with pytest.raises(EgressBlocked):
        check('{"tel": 612345678}', vault_with((EntityType.PHONE, "612 345 678")))


def test_one_megabyte_with_200_values_is_fast() -> None:
    # Generous bound (measured well under it) so a slow CI runner does not make it flaky.
    import time

    values = [f"{n:08d}" + "TRWAGMYFPDXBNJZSQVHLCKE"[n % 23] for n in range(10_000_000, 10_000_200)]
    vault = vault_with(*((EntityType.ES_DNI, v) for v in values))
    filler = "texto de relleno sin datos " * 190
    payload = json.dumps({"messages": [{"content": filler} for _ in range(200)]})
    assert len(payload) > 1_000_000
    start = time.perf_counter()
    check(payload, vault)
    assert time.perf_counter() - start < 2.0


def test_word_boundary_for_short_values() -> None:
    vault = vault_with((EntityType.ADDRESS, "Ana"))
    check("la semana que viene", vault)
    with pytest.raises(EgressBlocked):
        check("hola, Ana.", vault)


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


# --- Names found by the NER (issue 6, ADR-0016) -----------------------------------------------


@pytest.mark.parametrize("payload", ["la semana que viene", "Banana", "Anabel", "mañana"])
def test_a_short_name_needs_word_boundaries(payload: str) -> None:
    check(payload, vault_with((EntityType.PERSON, "Ana")))


@pytest.mark.parametrize(
    "payload", ["Soy Ana.", "ANA", "ana,", '{"n": "' + chr(92) + 'u0041na"}', "Ána"]
)
def test_a_short_name_between_boundaries_blocks(payload: str) -> None:
    with pytest.raises(EgressBlocked):
        check(payload, vault_with((EntityType.PERSON, "Ana")))


@pytest.mark.parametrize(
    "payload",
    [
        "Carmen Prueba López",
        "carmen prueba lopez",
        "CARMEN-PRUEBA-LÓPEZ",
        "CarmenPruebaLópez",
        "xcarmenpruebalopezx",
    ],
)
def test_a_long_name_is_found_in_any_spelling_and_glued(payload: str) -> None:
    with pytest.raises(EgressBlocked):
        check(payload, vault_with((EntityType.PERSON, "Carmen Prueba López")))


def test_a_long_name_inside_another_word_blocks_an_accepted_false_positive() -> None:
    # Documented in ADR-0016: 6+ letters are compared without word boundaries, so "Marina"
    # blocks "submarina". The masker masks it too (propagation), so this rarely blocks.
    with pytest.raises(EgressBlocked):
        check("un submarina amarillo", vault_with((EntityType.PERSON, "Marina")))


def test_a_name_written_with_look_alike_letters_blocks() -> None:
    """Review of 6a: the guard folds the same Cyrillic and Greek look-alikes as the detector."""
    cyrillic_a, greek_o = chr(0x430), chr(0x3BF)
    vault = vault_with((EntityType.PERSON, "Carmen Prueba López"))
    with pytest.raises(EgressBlocked):
        check(f"C{cyrillic_a}rmen Prueba L{greek_o}pez", vault)
    with pytest.raises(EgressBlocked):
        check(f"Hola {cyrillic_a.upper()}na", vault_with((EntityType.PERSON, "Ana")))
