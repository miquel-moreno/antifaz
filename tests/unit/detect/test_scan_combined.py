"""scan() joins identifiers and patterns and resolves their overlaps. All data is synthetic.

ES4400120345010612345678 is built here: valid CCC and IBAN controls, account 0612345678.
"""

from collections.abc import Callable
from itertools import pairwise

import pytest

from antifaz.detect.scan import scan
from antifaz.detect.types import Confidence, EntityType, Layer
from antifaz.detect.validators import luhn
from tests.conftest import SENTINEL_DNI
from tests.timing import Work, assert_linear

T = EntityType


def _found(text: str) -> list[tuple[str, EntityType]]:
    return [(text[s.start : s.end], s.type) for s in scan(text)]


@pytest.mark.parametrize(
    "value",
    [
        "ES4400120345010612345678",
        "ES44 0012 0345 01 0612345678",
        "ES44 0012 0345 0106 1234 5678",
    ],
)
def test_an_iban_holding_phone_like_digits_is_reported_only_as_iban(value: str) -> None:
    text = f"IBAN {value}"
    assert _found(text) == [(value, T.IBAN)]


def test_a_dni_next_to_a_phone_gives_two_spans() -> None:
    text = f"DNI {SENTINEL_DNI}, tel. 612345678"
    spans = scan(text)
    assert [(text[s.start : s.end], s.type) for s in spans] == [
        (SENTINEL_DNI, T.ES_DNI),
        ("612345678", T.PHONE),
    ]
    assert spans[1].confidence is Confidence.HIGH


@pytest.mark.parametrize(
    "email", ["ana612345678@example.com", "user.600123456@example.com", "ana.1985@example.com"]
)
def test_an_email_holding_digits_is_one_email_span(email: str) -> None:
    text = f"Escribe a {email} hoy"
    assert _found(text) == [(email, T.EMAIL)]


def test_a_grouped_card_starting_with_6_is_reported_only_as_a_card() -> None:
    text = "tarjeta 6011 1111 1111 1117"
    spans = scan(text)
    assert [(text[s.start : s.end], s.type) for s in spans] == [
        ("6011 1111 1111 1117", T.CREDIT_CARD)
    ]
    assert spans[0].layer is Layer.VALIDATOR


def test_a_spaced_french_nir_is_reported_only_as_nir() -> None:
    # stdnum.fr.nir docstring example; "95 10 99 126" inside it looks like a phone.
    text = "sécurité sociale : 2 95 10 99 126 111 93"
    assert _found(text) == [("2 95 10 99 126 111 93", T.FR_NIR)]


def test_patterns_and_identifiers_come_out_in_order_of_position() -> None:
    text = (
        f"Cliente con DNI {SENTINEL_DNI}, email ana@example.com, móvil 612 345 678, "
        "vive en C/ Mayor 3, 2º B, 08001 Barcelona; paga con 4111 1111 1111 1111 "
        "desde la IP 192.168.1.10."
    )
    assert _found(text) == [
        (SENTINEL_DNI, T.ES_DNI),
        ("ana@example.com", T.EMAIL),
        ("612 345 678", T.PHONE),
        ("C/ Mayor 3, 2º B, 08001", T.ADDRESS),
        ("4111 1111 1111 1111", T.CREDIT_CARD),
        ("192.168.1.10", T.IP),
    ]


def test_context_entities_are_found_by_scan() -> None:
    text = (
        "pasaporte AAA123456, matrícula 1234 BCD, nacido el 12/03/1985, "
        "Steuer-ID 36574261809"  # stdnum.de.idnr docstring example
    )
    assert _found(text) == [
        ("AAA123456", T.ES_PASSPORT),
        ("1234 BCD", T.ES_PLATE),
        ("12/03/1985", T.DATE_OF_BIRTH),
        ("36574261809", T.DE_IDNR),
    ]


def test_scan_output_never_overlaps_with_patterns_on() -> None:
    text = "tel 612345678 IBAN ES4400120345010612345678 ana612345678@example.com"
    spans = scan(text)
    assert len(spans) == 3
    for left, right in pairwise(spans):
        assert left.end <= right.start


# --- speed (ADR-0008): adversarial inputs, then many real values in one text --------------

# A head, a unit repeated n times, a tail and the size of the old fixed-time test; the speed
# is the shape of tests/timing.py, with 4n at least the old size.
ADVERSARIAL = [
    ("", "a", "@", 50_000),
    ("", "a@", "", 25_000),
    ("", "a.", "", 25_000),
    ("", "a@b.", "", 12_500),
    ("x@", "a.", "", 25_000),
    ("", "a-", "", 25_000),
    ("", ".", "", 50_000),
    ("", "-", "", 50_000),
    ("", "1.", "", 25_000),
    ("", "1.1.1.", "", 8_000),
    ("", "255.", "", 12_500),
    ("", "6", "", 50_000),
    ("", "6 ", "", 25_000),
    ("", "6.", "", 25_000),
    ("", "+34 ", "", 12_500),
    ("", "(+34) ", "", 8_000),
    ("", "4", "", 50_000),
    ("", "4111 ", "", 10_000),
    ("", "4111-", "", 10_000),
    ("", "C/ ", "", 10_000),
    ("Calle ", "Mayor ", "", 10_000),
    ("", "Calle de ", "", 6_000),
    ("", "Calle Mayor 3, ", "", 3_000),
    ("", "2º B, ", "", 8_000),
    ("", "pasaporte ", "", 5_000),
    ("", "pasaporte AAA", "", 4_000),
    ("", "matrícula 1234 ", "", 4_000),
    ("", "nacido el 12/03/", "", 4_000),
    ("", "nacido el 12 de marzo de ", "", 2_000),
    ("", "Steuer-ID 36 574 ", "", 3_000),
    ("", "tel ", "", 12_500),
]


@pytest.mark.parametrize(
    ("head", "unit", "tail", "size"),
    ADVERSARIAL,
    ids=[repr(h + u + t) for h, u, t, _ in ADVERSARIAL],
)
def test_scan_of_long_adversarial_input_is_fast(head: str, unit: str, tail: str, size: int) -> None:
    def prepare(n: int) -> Work:
        text = head + unit * n + tail
        return lambda: scan(text)

    assert_linear(prepare, start=size // 4)


def _visa(i: int) -> str:
    body = f"4{i:014d}"
    return body + str(luhn.check_digit(body))


# Many real values in one text: a builder for n values, the type and the old count (house
# numbers stay under 10 000 and the passport keeps six digits, whatever n calibrates to).
MANY = [
    (lambda n: " ".join(f"usuario{i}@example.com" for i in range(n)), T.EMAIL, 10_000),
    (lambda n: ", ".join(f"6{i:08d}" for i in range(n)), T.PHONE, 10_000),
    (lambda n: " ".join(_visa(i) for i in range(n)), T.CREDIT_CARD, 5_000),
    (lambda n: "; ".join(f"C/ Mayor {i % 9_999 + 1}, 08001" for i in range(n)), T.ADDRESS, 5_000),
    (lambda n: " ".join(f"pasaporte AAA{i:06d}" for i in range(n)), T.ES_PASSPORT, 5_000),
]


@pytest.mark.parametrize(("build", "entity_type", "size"), MANY, ids=[m[1].value for m in MANY])
def test_many_real_values_in_one_text_are_all_found_quickly(
    build: Callable[[int], str], entity_type: EntityType, size: int
) -> None:
    def prepare(n: int) -> Work:
        text = build(n)

        def work() -> None:
            spans = scan(text)
            assert sum(1 for s in spans if s.type is entity_type) == n

        return work

    assert_linear(prepare, start=size // 4, limit=999_999)


# ADR-0010 and the privacy review of PR 2b: no piece of an email is left in clear.
@pytest.mark.parametrize(
    "value",
    [
        "12345678Z@example.com",
        "juan.garcia.12345678Z@example.com",
        "4111111111111111@example.es",
        "Juan.Pérez@example.es",
        "o'brien@example.com",
        "juan&co@example.com",
        "josé@example.es",
        "juan@münchen.example",
    ],
)
def test_an_email_is_masked_whole(value: str) -> None:
    text = f"Escribe a {value} hoy"
    assert [(text[s.start : s.end], s.type) for s in scan(text)] == [(value, T.EMAIL)]
