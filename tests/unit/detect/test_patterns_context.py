"""has_context() and the entities that are only reported with a keyword before them.

Passport, plate and dates are invented. The Portuguese, French and German numbers are the
examples in python-stdnum's own docstrings (stdnum.pt.nif, stdnum.fr.nir, stdnum.de.idnr).
"""

import re

import pytest

from antifaz.detect.patterns.context import has_context
from antifaz.detect.patterns.personal import find_patterns
from antifaz.detect.types import Confidence, EntityType, Layer

T = EntityType
TEL = re.compile(r"\btel\b")


def _found(text: str, entity_type: EntityType) -> list[str]:
    spans = sorted(find_patterns(text), key=lambda s: s.start)
    return [text[s.start : s.end] for s in spans if s.type is entity_type]


def _assert_single(
    text: str, value: str, entity_type: EntityType, layer: Layer, confidence: Confidence
) -> None:
    spans = [s for s in find_patterns(text) if s.type is entity_type]
    assert [text[s.start : s.end] for s in spans] == [value]
    assert spans[0].layer is layer
    assert spans[0].confidence is confidence


# --- has_context ------------------------------------------------------------------------


def test_keyword_right_before_the_value_is_context() -> None:
    assert has_context("tel 600", 4, TEL)


def test_keyword_whose_start_is_exactly_40_characters_before_is_context() -> None:
    text = "tel" + " " * 37 + "X"
    assert has_context(text, len(text) - 1, TEL)


def test_keyword_41_characters_before_is_not_context() -> None:
    text = "tel" + " " * 38 + "X"
    assert not has_context(text, len(text) - 1, TEL)


def test_window_can_be_changed() -> None:
    text = "tel" + " " * 7 + "X"
    assert has_context(text, len(text) - 1, TEL, window=10)
    assert not has_context(text, len(text) - 1, TEL, window=9)


def test_keyword_after_the_start_is_not_context() -> None:
    assert not has_context("600 tel", 0, TEL)
    assert not has_context("X tel", 1, TEL)


def test_nothing_before_the_start_of_the_text_is_context() -> None:
    assert not has_context("tel", 0, TEL)
    assert not has_context("", 0, TEL)


def test_case_is_up_to_the_pattern() -> None:
    assert not has_context("TEL 600", 4, TEL)
    assert has_context("TEL 600", 4, re.compile(r"\btel\b", re.IGNORECASE))


def test_the_window_edge_does_not_turn_part_of_a_word_into_a_keyword() -> None:
    # The window starts at the "t" of "hotel": "\btel" must still see the "ho" before it.
    text = "hotel" + " " * 37 + "X"
    assert not has_context(text, len(text) - 1, TEL)


# --- ES_PASSPORT --------------------------------------------------------------------------

PASSPORTS = [
    ("pasaporte AAA123456", "AAA123456"),
    ("Pasaporte: PAB654321.", "PAB654321"),
    ("Passport XDA123456", "XDA123456"),
    ("nº de pasap. ABC123456", "ABC123456"),
    ("PASAPORTE N.º ZZZ000001", "ZZZ000001"),
]


@pytest.mark.parametrize(("text", "value"), PASSPORTS, ids=[p[0] for p in PASSPORTS])
def test_passport_with_keyword_is_found(text: str, value: str) -> None:
    _assert_single(text, value, T.ES_PASSPORT, Layer.PATTERN, Confidence.MEDIUM)


@pytest.mark.parametrize(
    "text",
    [
        "AAA123456",  # no keyword
        "ref ABC123456",
        "pasaporte AB1234567",  # 2 letters + 7 digits
        "pasaporte AAAA123456",  # inside a longer alphanumeric run
        "pasaporte AAA1234567",
        "pasaporte AAA12345",
        "pasaporte" + " " * 50 + "AAA123456",  # keyword too far away
        "AAA123456 es mi pasaporte",  # keyword after the value
    ],
)
def test_passport_like_value_without_valid_context_or_shape_is_not_reported(text: str) -> None:
    assert _found(text, T.ES_PASSPORT) == []


# --- ES_PLATE ---------------------------------------------------------------------------

PLATES = [
    ("matrícula 1234 BCD", "1234 BCD"),
    ("Matricula: 1234-BCD", "1234-BCD"),
    ("el coche 1234BCD", "1234BCD"),
    ("Vehículo 0000 XYZ", "0000 XYZ"),
    ("vehiculo 5678 GHJ.", "5678 GHJ"),
    ("moto 9876 KLM", "9876 KLM"),
    ("placa 4321 NPR", "4321 NPR"),
    ("MATRÍCULA 1111 STV", "1111 STV"),
]


@pytest.mark.parametrize(("text", "value"), PLATES, ids=[p[0] for p in PLATES])
def test_plate_with_keyword_is_found(text: str, value: str) -> None:
    _assert_single(text, value, T.ES_PLATE, Layer.PATTERN, Confidence.MEDIUM)


@pytest.mark.parametrize(
    "text",
    [
        "1234 BCD",  # no keyword
        "matrícula 1234 BAD",  # vowel
        "matrícula 1234 BCE",
        "matrícula 1234 BCQ",  # Q
        "matrícula 1234 BCÑ",  # Ñ
        "matrícula 12345 BCD",
        "matrícula 123 BCD",
        "matrícula 1234 BCDF",
        "matrícula 1234 BC",
        "matrícula" + " " * 50 + "1234 BCD",  # keyword too far away
    ],
)
def test_plate_like_value_without_valid_context_or_shape_is_not_reported(text: str) -> None:
    assert _found(text, T.ES_PLATE) == []


# --- DATE_OF_BIRTH --------------------------------------------------------------------------

BIRTH_DATES = [
    ("nacido el 12/03/1985", "12/03/1985"),
    ("Nacida el 01-01-1990.", "01-01-1990"),
    ("Fecha de nacimiento: 05.11.1978", "05.11.1978"),
    ("F. nac.: 28/02/1970", "28/02/1970"),
    ("fecha de nac. 30/06/2001", "30/06/2001"),
    ("born 12/03/1985", "12/03/1985"),
    ("DOB: 29/02/2000", "29/02/2000"),  # 2000 is a leap year
    ("nacido el 1/3/1985", "1/3/1985"),  # one-digit day and month
    ("nacida el 12 de marzo de 1985", "12 de marzo de 1985"),
    ("nacimiento: 3 de Septiembre de 1970", "3 de Septiembre de 1970"),
    ("NACIDO EL 1 DE ENERO DE 2001", "1 DE ENERO DE 2001"),
]


@pytest.mark.parametrize(("text", "value"), BIRTH_DATES, ids=[d[0] for d in BIRTH_DATES])
def test_date_of_birth_with_keyword_is_found(text: str, value: str) -> None:
    _assert_single(text, value, T.DATE_OF_BIRTH, Layer.PATTERN, Confidence.MEDIUM)


@pytest.mark.parametrize(
    "text",
    [
        "12/03/1985",  # no keyword
        "fecha de la factura: 12/03/1985",
        "nacido el 31/02/1990",  # invalid date
        "nacido el 29/02/1999",  # 1999 is not a leap year
        "nacido el 12/13/1985",
        "nacido el 00/03/1985",
        "nacido el 32 de marzo de 1985",
        "nacido el 30 de febrero de 1985",
        "nacido el 12 de marzi de 1985",
        "nacido el 112/03/1985",  # inside a longer digit run
        "nacido" + " " * 50 + "12/03/1985",  # keyword too far away
    ],
)
def test_date_without_birth_context_or_invalid_is_not_reported(text: str) -> None:
    assert _found(text, T.DATE_OF_BIRTH) == []


# --- PT_NIF, FR_NIR, DE_IDNR -----------------------------------------------------------------

EU_IDS = [
    ("contribuinte 501964843", "501964843", T.PT_NIF),
    ("NIF PT 501964843", "501964843", T.PT_NIF),
    ("Portugal, NIF: 501964843.", "501964843", T.PT_NIF),
    ("NIR 295109912611193", "295109912611193", T.FR_NIR),
    ("sécurité sociale : 2 95 10 99 126 111 93", "2 95 10 99 126 111 93", T.FR_NIR),
    ("securite sociale 295109912611193", "295109912611193", T.FR_NIR),
    ("numéro INSEE 295109912611193", "295109912611193", T.FR_NIR),
    ("Steuer-ID 36574261809", "36574261809", T.DE_IDNR),
    ("Steuer-ID: 36 574 261 809", "36 574 261 809", T.DE_IDNR),
    ("Steueridentifikationsnummer 36574261809", "36574261809", T.DE_IDNR),
    ("IdNr 36574261809", "36574261809", T.DE_IDNR),
    ("Identifikationsnummer: 36574261809", "36574261809", T.DE_IDNR),
]


@pytest.mark.parametrize(("text", "value", "entity_type"), EU_IDS, ids=[e[0] for e in EU_IDS])
def test_digit_only_eu_identifier_with_keyword_is_found(
    text: str, value: str, entity_type: EntityType
) -> None:
    _assert_single(text, value, entity_type, Layer.VALIDATOR, Confidence.HIGH)


@pytest.mark.parametrize(
    ("text", "entity_type"),
    [
        ("501964843", T.PT_NIF),  # no keyword: a 9-digit NIF looks like a Spanish phone
        ("contribuinte 501964842", T.PT_NIF),  # wrong check digit (stdnum docstring)
        ("295109912611193", T.FR_NIR),
        ("NIR 295109912611199", T.FR_NIR),  # wrong check digit (stdnum docstring)
        ("36574261809", T.DE_IDNR),
        ("Steuer-ID 36574261890", T.DE_IDNR),  # wrong check digit (stdnum docstring)
        ("IdNr 36554266806", T.DE_IDNR),  # too many repeated digits (stdnum docstring)
        ("contribuinte 5019648431", T.PT_NIF),  # inside a longer digit run
        ("Steuer-ID" + " " * 50 + "36574261809", T.DE_IDNR),  # keyword too far away
    ],
)
def test_digit_only_eu_identifier_without_keyword_or_invalid_is_not_reported(
    text: str, entity_type: EntityType
) -> None:
    assert _found(text, entity_type) == []


def test_a_keyword_for_one_country_does_not_report_another_countrys_identifier() -> None:
    text = "contribuinte 36574261809"  # a valid German IdNr after a Portuguese keyword
    assert _found(text, T.DE_IDNR) == []
    assert _found(text, T.PT_NIF) == []


def test_a_failing_eu_validator_rejects_the_value_instead_of_raising() -> None:
    import re as _re

    from antifaz.detect.patterns.personal import _validated_by

    def broken(value: str) -> bool:
        raise RuntimeError(f"stdnum failed on {value}")  # the value must not escape

    match = _re.search(r"[0-9]+", "501964843")
    assert match is not None
    assert _validated_by(broken)(match) is False
