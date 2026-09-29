"""Email, IP and phone patterns. All addresses and numbers are invented."""

import logging

import pytest

from antifaz.detect.patterns.personal import find_patterns
from antifaz.detect.types import Confidence, EntityType, Layer

T = EntityType


def _found(text: str, entity_type: EntityType) -> list[tuple[str, Confidence]]:
    """(value, confidence) of every span of that type, in order of position."""
    spans = sorted(find_patterns(text), key=lambda s: s.start)
    return [(text[s.start : s.end], s.confidence) for s in spans if s.type is entity_type]


def test_find_patterns_returns_in_bounds_pattern_spans_for_contact_data() -> None:
    text = "Escribe a ana.garcia@example.com o llama al 612345678 (IP 10.0.0.1)"
    spans = find_patterns(text)
    assert {s.type for s in spans} >= {T.EMAIL, T.PHONE, T.IP}
    for span in spans:
        assert 0 <= span.start < span.end <= len(text)
        if span.type in (T.EMAIL, T.PHONE, T.IP):
            assert span.layer is Layer.PATTERN


# --- EMAIL --------------------------------------------------------------------------

EMAILS = [
    ("Escribe a ana.garcia@example.com.", "ana.garcia@example.com"),
    ("ana+facturas@correo.example.es", "ana+facturas@correo.example.es"),
    ("soporte: soporte@mail.eu.example.org, gracias", "soporte@mail.eu.example.org"),
    ("ANA_G-1@Example.COM", "ANA_G-1@Example.COM"),
    ("Contacto <jordi.puig@example.org>", "jordi.puig@example.org"),
    ("mailto:ana@example.com", "ana@example.com"),
    ("(ana.1985@example.net)", "ana.1985@example.net"),
]


@pytest.mark.parametrize(("text", "value"), EMAILS, ids=[e[1] for e in EMAILS])
def test_email_is_found_with_exact_boundaries_and_high_confidence(text: str, value: str) -> None:
    assert _found(text, T.EMAIL) == [(value, Confidence.HIGH)]


def test_two_emails_in_a_sentence_are_both_found() -> None:
    text = "Copia a ana@example.com y a pere@example.org."
    assert [v for v, _ in _found(text, T.EMAIL)] == ["ana@example.com", "pere@example.org"]


@pytest.mark.parametrize(
    "text",
    [
        "a@b",  # no dot-TLD
        "a@b.c",  # one-letter TLD
        "sígueme en @handle",
        "user@localhost",
        "ana@example.",  # the dot ends the sentence, there is no TLD
        "@example.com",
        "ana@",
        "",
    ],
)
def test_things_that_are_not_emails_are_not_reported(text: str) -> None:
    assert _found(text, T.EMAIL) == []


# --- IP -----------------------------------------------------------------------------

IPS = [
    ("El servidor 192.168.1.10 no responde", "192.168.1.10"),
    ("IP: 10.0.0.1, puerto 22", "10.0.0.1"),
    ("desde 203.0.113.8.", "203.0.113.8"),  # the sentence period is not part of it
    ("0.0.0.0", "0.0.0.0"),  # noqa: S104 (a string to scan, not a bind address)
    ("máscara 255.255.255.255", "255.255.255.255"),
    ("(172.16.254.1)", "172.16.254.1"),
]


@pytest.mark.parametrize(("text", "value"), IPS, ids=[i[1] for i in IPS])
def test_ipv4_is_found_with_exact_boundaries_and_medium_confidence(text: str, value: str) -> None:
    assert _found(text, T.IP) == [(value, Confidence.MEDIUM)]


@pytest.mark.parametrize(
    "text",
    [
        "256.1.1.1",  # octet out of range
        "10.0.0.300",
        "versión 1.2.3.4.5",  # part of a longer dotted run
        "1.2.3",
        "build v10.0.0.1",  # preceded by a letter (decision: not an IP)
        "10.0.0.1a",  # followed by a letter
        "110.0.0.1.2",
        "fecha 12.03.1985",
    ],
)
def test_things_that_are_not_ipv4_are_not_reported(text: str) -> None:
    assert _found(text, T.IP) == []


# --- PHONE --------------------------------------------------------------------------

PHONES_WITHOUT_CONTEXT = [
    ("Número 600123456 de contacto", "600123456"),
    ("600 123 456", "600 123 456"),
    ("600 12 34 56", "600 12 34 56"),
    ("600.123.456", "600.123.456"),
    ("600-123-456", "600-123-456"),
    ("712345678", "712345678"),
    ("876543210", "876543210"),
    ("Oficina 912 34 56 78.", "912 34 56 78"),
    ("Oficina 91 234 56 78", "91 234 56 78"),  # Madrid landline grouping
    ("+34 600-123-456", "+34 600-123-456"),  # the prefix is part of the span
    ("+34600123456", "+34600123456"),
    ("0034 912345678", "0034 912345678"),
    ("0034600123456", "0034600123456"),
    ("(+34) 612345678", "(+34) 612345678"),
]


@pytest.mark.parametrize(
    ("text", "value"), PHONES_WITHOUT_CONTEXT, ids=[p[1] for p in PHONES_WITHOUT_CONTEXT]
)
def test_spanish_phone_without_context_is_found_with_medium_confidence(
    text: str, value: str
) -> None:
    assert _found(text, T.PHONE) == [(value, Confidence.MEDIUM)]


PHONES_WITH_CONTEXT = [
    ("tel. 600123456", "600123456"),
    ("Tel: 600 123 456", "600 123 456"),
    ("Teléfono: 600 12 34 56", "600 12 34 56"),
    ("telefono 912345678", "912345678"),
    ("Mi móvil es el +34 612 345 678", "+34 612 345 678"),
    ("movil 612345678", "612345678"),
    ("llama al 612-345-678", "612-345-678"),
    ("puedes llamar al 612.345.678", "612.345.678"),
    ("WhatsApp 612345678", "612345678"),
    ("TELÉFONO DE CONTACTO: 612345678", "612345678"),
]


@pytest.mark.parametrize(
    ("text", "value"), PHONES_WITH_CONTEXT, ids=[p[0] for p in PHONES_WITH_CONTEXT]
)
def test_phone_with_a_keyword_before_has_high_confidence(text: str, value: str) -> None:
    assert _found(text, T.PHONE) == [(value, Confidence.HIGH)]


def test_phone_keyword_further_than_40_characters_does_not_raise_confidence() -> None:
    text = "tel" + " y aquí sigue un texto largo sin nada más " + "612345678"
    assert text.index("612345678") > 40
    assert _found(text, T.PHONE) == [("612345678", Confidence.MEDIUM)]


def test_phone_keyword_inside_another_word_does_not_raise_confidence() -> None:
    assert _found("Reservé el hotel 612345678", T.PHONE) == [("612345678", Confidence.MEDIUM)]


def test_phone_keyword_after_the_number_does_not_raise_confidence() -> None:
    assert _found("612345678 es mi móvil", T.PHONE) == [("612345678", Confidence.MEDIUM)]


def test_several_phones_are_all_found_in_order() -> None:
    text = "Llama al 600123456, al 612 345 678 o al 912345678."
    assert [v for v, _ in _found(text, T.PHONE)] == ["600123456", "612 345 678", "912345678"]


# 800/900/901/902/905: business numbers; 803/806/807: premium rate (CNMC).
BUSINESS_NUMBERS = [
    "800123456",
    "900 123 456",
    "901123456",
    "902 12 34 56",
    "905-123-456",
    "803123456",
    "806123456",
    "807123456",
    "+34 900123456",
    "0034 902123456",
]


@pytest.mark.parametrize("number", BUSINESS_NUMBERS)
@pytest.mark.parametrize("prefix", ["", "tel. "])
def test_business_and_premium_rate_numbers_are_not_phones(prefix: str, number: str) -> None:
    assert _found(prefix + number, T.PHONE) == []


@pytest.mark.parametrize(
    "text",
    [
        "Pedido 1600123456",  # 10 digits
        "6001234567",
        "ref 123600123456",  # a 12-digit number
        "600123456789",
        "500123456",  # first digit 5
        "60012345",  # 8 digits
        "ref A600123456",  # inside an alphanumeric code
        "600123456B",
        "",
        "sin números",
    ],
)
def test_digit_runs_that_are_not_phones_are_not_reported(text: str) -> None:
    assert _found(text, T.PHONE) == []


# --- privacy --------------------------------------------------------------------------


def test_find_patterns_writes_nothing_to_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    email, phone = "centinela.privado@example.com", "699887766"
    text = f"Escribe a {email} o llama al {phone}"
    spans = find_patterns(text)
    assert {text[s.start : s.end] for s in spans} >= {email, phone}
    for value in (email, phone):
        assert value not in caplog.text
        for record in caplog.records:
            assert value not in record.getMessage()
            assert value not in repr(record.args)


# Found by the code review of PR 2b: phones next to other numbers.
@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("Tel 612345678 698765432", ["612345678", "698765432"]),
        ("Tel 612345678-698765432", ["612345678", "698765432"]),
        ("Tel 612 345 678 698765432", ["612 345 678", "698765432"]),
        ("Tel 612345678 2 veces", ["612345678"]),
        ("cita 3 612345678", ["612345678"]),
        ("móviles: 612 345 678, 698 765 432", ["612 345 678", "698 765 432"]),
        ("wa.me/34612345678", ["34612345678"]),
    ],
)
def test_phones_next_to_other_numbers_are_found(text: str, values: list[str]) -> None:
    assert [v for v, _ in _found(text, T.PHONE)] == values


def test_a_phone_group_is_not_read_as_part_of_a_card() -> None:
    from antifaz.detect.scan import scan

    text = "Tel 612 345 678 698765432"
    assert [(text[s.start : s.end], s.type) for s in scan(text)] == [
        ("612 345 678", T.PHONE),
        ("698765432", T.PHONE),
    ]
