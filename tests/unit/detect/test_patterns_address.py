"""Spanish-style postal addresses. All addresses are invented or generic street names.

Span decision: the span covers street type + name + number + optional floor and door +
optional postcode. The city after the postcode is not included (place names are the NER's
job) and neither is the punctuation that ends the sentence.
"""

import pytest

from antifaz.detect.patterns.personal import find_patterns
from antifaz.detect.types import Confidence, EntityType, Layer

T = EntityType
H, M = Confidence.HIGH, Confidence.MEDIUM


def _addresses(text: str) -> list[tuple[str, Confidence]]:
    spans = sorted(find_patterns(text), key=lambda s: s.start)
    return [(text[s.start : s.end], s.confidence) for s in spans if s.type is T.ADDRESS]


ADDRESSES = [
    ("Vivo en C/ Mayor 3, 2º B, 08001 Barcelona", "C/ Mayor 3, 2º B, 08001", H),
    ("en la Calle de la Paz 12.", "Calle de la Paz 12", M),
    ("Avda. Diagonal 640, 5º 2ª", "Avda. Diagonal 640, 5º 2ª", M),
    ("Plaza España nº 5", "Plaza España nº 5", M),
    ("Paseo de Gracia 21, 08007 Barcelona", "Paseo de Gracia 21, 08007", H),
    ("Calle Núñez de Balboa 3", "Calle Núñez de Balboa 3", M),
    ("Calle de la Paz 12, 28012 Madrid", "Calle de la Paz 12, 28012", H),
    ("Avenida de la Constitución 15", "Avenida de la Constitución 15", M),
    ("Av. Meridiana 350, 1º", "Av. Meridiana 350, 1º", M),
    ("Pl. Catalunya 1", "Pl. Catalunya 1", M),
    ("Pº de la Castellana 100", "Pº de la Castellana 100", M),
    ("Ctra. de Valencia 5", "Ctra. de Valencia 5", M),
    ("Carretera de Castilla 12", "Carretera de Castilla 12", M),
    ("Camino Viejo 7", "Camino Viejo 7", M),
    ("Ronda de Sant Pere 19", "Ronda de Sant Pere 19", M),
    ("Travesía del Mar 2", "Travesía del Mar 2", M),
    ("C/ Mayor, 3", "C/ Mayor, 3", M),  # comma between name and number
    ("Calle Mayor, 3, 01001 Vitoria", "Calle Mayor, 3, 01001", H),  # lowest postcode prefix
    ("C/ Mayor nº 3", "C/ Mayor nº 3", M),
    ("Calle Mayor 3, y luego a la derecha", "Calle Mayor 3", M),
    ("C/ Mayor 3, 2ºB, 52001 Melilla", "C/ Mayor 3, 2ºB, 52001", H),  # highest prefix
]


@pytest.mark.parametrize(("text", "value", "confidence"), ADDRESSES, ids=[a[1] for a in ADDRESSES])
def test_address_is_found_with_exact_span_and_confidence(
    text: str, value: str, confidence: Confidence
) -> None:
    spans = [s for s in find_patterns(text) if s.type is T.ADDRESS]
    assert [(text[s.start : s.end], s.confidence) for s in spans] == [(value, confidence)]
    assert spans[0].layer is Layer.PATTERN


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Calle de la Paz 12, 53000", "Calle de la Paz 12"),  # 53 is not a province
        ("Calle de la Paz 12, 00100", "Calle de la Paz 12"),  # 00 is not a province
        ("Calle de la Paz 12, 99999", "Calle de la Paz 12"),
    ],
)
def test_a_postcode_outside_01000_52999_is_left_out_and_confidence_stays_medium(
    text: str, value: str
) -> None:
    assert _addresses(text) == [(value, M)]


def test_two_addresses_are_both_found() -> None:
    text = "De C/ Mayor 3 a Avda. Diagonal 640, 08019 Barcelona."
    assert _addresses(text) == [("C/ Mayor 3", M), ("Avda. Diagonal 640, 08019", H)]


@pytest.mark.parametrize(
    "text",
    [
        "vivo en la calle",
        "la calle Mayor es bonita",
        "Plaza España",  # no number
        "Avda.",
        "C/ ",
        "Calle",
        "el camino es largo",
        "Recalle Mayor 3",  # the street type is inside another word
        "",
    ],
)
def test_street_words_without_a_full_address_are_not_reported(text: str) -> None:
    assert _addresses(text) == []
