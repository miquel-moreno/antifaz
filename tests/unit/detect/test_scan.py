"""Finding identifiers in free text. All values are synthetic."""

import logging
import time

import pytest

from antifaz.detect.patterns.identifiers import find_identifiers
from antifaz.detect.scan import scan
from antifaz.detect.types import Confidence, EntityType, Layer
from tests.conftest import SENTINEL_DNI

T = EntityType

CASES = [
    ("Mi DNI es 12345678Z.", "12345678Z", T.ES_DNI),
    ("DNI: 12345678-Z, gracias", "12345678-Z", T.ES_DNI),
    ("DNI 12.345.678-Z en el contrato", "12.345.678-Z", T.ES_DNI),
    ("12345678Z", "12345678Z", T.ES_DNI),
    ("NIE X2482300W del cliente", "X2482300W", T.ES_NIE),
    ("NIE: X-2482300-W", "X-2482300-W", T.ES_NIE),
    ("NIF K1234567L sin DNI", "K1234567L", T.ES_NIF),
    ("Empresa con CIF B64717838.", "B64717838", T.ES_CIF),
    ("CIF P-1234567-D", "P-1234567-D", T.ES_CIF),
    ("NSS 28 12345678 40 de alta", "28 12345678 40", T.ES_NSS),
    ("NSS: 28/12345678/40", "28/12345678/40", T.ES_NSS),
    ("cuenta 0012 0345 03 0000067890 en el banco", "0012 0345 03 0000067890", T.ES_CCC),
    ("IBAN ES07 0012 0345 03 0000067890.", "ES07 0012 0345 03 0000067890", T.IBAN),
    ("IBAN: ES0700120345030000067890", "ES0700120345030000067890", T.IBAN),
    ("cuenta DE89 3704 0044 0532 0130 00 en Alemania", "DE89 3704 0044 0532 0130 00", T.IBAN),
    ("codice fiscale RCCMNL83S18D969H", "RCCMNL83S18D969H", T.IT_CODICE_FISCALE),
    ("VAT FR61954506077 (Francia)", "FR61954506077", T.EU_VAT),
]


@pytest.mark.parametrize(("text", "value", "entity_type"), CASES, ids=[c[1] for c in CASES])
def test_scan_finds_the_identifier_with_exact_boundaries(
    text: str, value: str, entity_type: EntityType
) -> None:
    spans = scan(text)
    assert len(spans) == 1
    (found,) = spans
    assert text[found.start : found.end] == value
    assert found.type is entity_type
    assert found.layer is Layer.VALIDATOR
    assert found.confidence is Confidence.HIGH


def test_find_identifiers_only_returns_validated_high_confidence_spans() -> None:
    text = "DNI 12345678Z, NIE X2482300W, IBAN ES07 0012 0345 03 0000067890"
    spans = find_identifiers(text)
    assert spans
    assert all(s.layer is Layer.VALIDATOR for s in spans)
    assert all(s.confidence is Confidence.HIGH for s in spans)
    assert all(0 <= s.start < s.end <= len(text) for s in spans)


def test_scan_finds_several_identifiers_in_order() -> None:
    text = "DNI 12345678Z y NIE X2482300W; NSS 28 12345678 40."
    spans = scan(text)
    assert [(text[s.start : s.end], s.type) for s in spans] == [
        ("12345678Z", T.ES_DNI),
        ("X2482300W", T.ES_NIE),
        ("28 12345678 40", T.ES_NSS),
    ]


def test_an_iban_is_reported_once_and_not_also_as_a_ccc() -> None:
    text = "IBAN ES07 0012 0345 03 0000067890"
    spans = scan(text)
    assert [(s.type, text[s.start : s.end]) for s in spans] == [
        (T.IBAN, "ES07 0012 0345 03 0000067890")
    ]


@pytest.mark.parametrize(
    "text",
    [
        "ref A12345678ZB",  # DNI inside a longer alphanumeric run
        "ref 912345678Z",  # preceded by another digit
        "ref 12345678ZZ",  # followed by another letter
        "ref X2482300WA",
        "DNI 12345678A",  # wrong letter
        "NIE X2482300A",  # wrong letter
        "cuenta 0012 0345 03 0000067891",  # wrong CCC control
        "IBAN ES6900120345990000067890",  # mod 97 right, CCC wrong
        "NSS 28 01234567 85",
        "",
        "sin datos personales",
    ],
)
def test_embedded_or_invalid_identifiers_are_not_reported(text: str) -> None:
    assert scan(text) == []


def test_scanning_the_sentinel_dni_writes_nothing_to_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    text = f"El cliente con DNI {SENTINEL_DNI} pide la baja"
    spans = scan(text)
    find_identifiers(text)
    assert [text[s.start : s.end] for s in spans] == [SENTINEL_DNI]
    assert SENTINEL_DNI not in caplog.text
    for record in caplog.records:
        assert SENTINEL_DNI not in record.getMessage()
        assert SENTINEL_DNI not in repr(record.args)


# ADR-0008: internal patterns must not backtrack. Long adversarial inputs must be scanned in
# linear time; the limit is generous so the test is not flaky on a slow CI machine.
ADVERSARIAL = [
    "1" * 50_000,
    "1 " * 25_000,
    "1-" * 25_000,
    "1." * 25_000,
    "1/" * 25_000,
    "X" + "1" * 50_000,
    "ES" * 25_000,
    "ES07 " * 10_000,
    "12.345." * 7_000,
    "A1" * 25_000,
]


@pytest.mark.parametrize("text", ADVERSARIAL, ids=lambda t: repr(t[:8]))
def test_scan_of_long_adversarial_input_is_fast(text: str) -> None:
    started = time.perf_counter()
    scan(text)
    assert time.perf_counter() - started < 2.0


def test_a_truncated_iban_is_not_reported() -> None:
    # Long enough to look like an IBAN, shorter than the 24 characters of a Spanish one.
    assert scan("IBAN ES07 0012 0345 0300 00") == []
