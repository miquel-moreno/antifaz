"""Payment card numbers: Luhn plus a known brand prefix.

4111111111111111, 4012888888881881, 4222222222222 (Visa), 5555555555554444,
2223003122003222 (Mastercard), 378282246310005 (Amex) and 6011111111111117 (Discover) are
the public test numbers published by the payment processors. The rest are built here with
the Luhn check digit and are not real cards.
"""

import pytest

from antifaz.detect.patterns.personal import find_patterns
from antifaz.detect.types import Confidence, EntityType, Layer
from antifaz.detect.validators import luhn

T = EntityType


def _card(prefix: str, length: int) -> str:
    """A Luhn-valid number of `length` digits starting with `prefix`, zeros in between."""
    body = prefix + "0" * (length - len(prefix) - 1)
    return body + str(luhn.check_digit(body))


def _cards(text: str) -> list[str]:
    spans = sorted(find_patterns(text), key=lambda s: s.start)
    return [text[s.start : s.end] for s in spans if s.type is T.CREDIT_CARD]


CARDS = [
    ("Visa 4111111111111111", "4111111111111111"),
    ("tarjeta 4111 1111 1111 1111.", "4111 1111 1111 1111"),
    ("tarjeta 4111-1111-1111-1111", "4111-1111-1111-1111"),
    ("4012888888881881", "4012888888881881"),
    ("Visa de 13 dígitos 4222222222222", "4222222222222"),
    ("Visa de 19 dígitos " + _card("4", 19), _card("4", 19)),
    ("MC 5555555555554444", "5555555555554444"),
    ("MC 5555 5555 5555 4444", "5555 5555 5555 4444"),
    ("MC " + _card("51", 16), _card("51", 16)),
    ("MC serie 2 2223003122003222", "2223003122003222"),
    ("MC serie 2 " + _card("2221", 16), _card("2221", 16)),
    ("MC serie 2 " + _card("2720", 16), _card("2720", 16)),
    ("Amex 378282246310005", "378282246310005"),
    ("Amex 3782 822463 10005", "3782 822463 10005"),
    ("Amex " + _card("34", 15), _card("34", 15)),
    ("Discover 6011111111111117", "6011111111111117"),
    ("Discover 6011 1111 1111 1117", "6011 1111 1111 1117"),
    ("Discover " + _card("65", 16), _card("65", 16)),
    ("Discover " + _card("644", 16), _card("644", 16)),
    ("Discover " + _card("649", 16), _card("649", 16)),
    ("(4111111111111111)", "4111111111111111"),
]


@pytest.mark.parametrize(("text", "value"), CARDS, ids=[c[0] for c in CARDS])
def test_card_with_valid_luhn_and_known_prefix_is_found(text: str, value: str) -> None:
    spans = [s for s in find_patterns(text) if s.type is T.CREDIT_CARD]
    assert [text[s.start : s.end] for s in spans] == [value]
    assert spans[0].layer is Layer.VALIDATOR
    assert spans[0].confidence is Confidence.HIGH


def test_two_contiguous_cards_separated_by_a_space_are_both_found() -> None:
    assert _cards("4111111111111111 5555555555554444") == ["4111111111111111", "5555555555554444"]


def test_two_grouped_cards_in_a_list_are_both_found() -> None:
    text = "Pruebas: 4111 1111 1111 1111, 5555-5555-5555-4444 y 378282246310005."
    assert _cards(text) == ["4111 1111 1111 1111", "5555-5555-5555-4444", "378282246310005"]


@pytest.mark.parametrize(
    "text",
    [
        "4111111111111112",  # Luhn wrong
        "4111 1111 1111 1112",
        "1234567812345670",  # Luhn right, unknown prefix
        _card("3527", 16),  # just before JCB 3528-3589
        _card("9", 16),
        _card("56", 16),  # just after the Mastercard 51-55 range
        _card("2721", 16),  # just after the Mastercard 2221-2720 range
        _card("2220", 16),  # just before it
        _card("643", 16),  # just before Discover 644-649
        _card("4", 12),  # too short
        _card("4", 20),  # too long
        "ref 41111111111111110000",  # inside a longer digit run
        "04111111111111111",
        "41111111111111111",
        "A4111111111111111",  # inside an alphanumeric code
        "4111111111111111B",
        "4111  1111  1111  1111",  # double spaces are not a card grouping
        "",
    ],
)
def test_numbers_that_are_not_cards_are_not_reported(text: str) -> None:
    assert _cards(text) == []


# Added with the privacy review of PR 2b: more brands and the dot separator.
@pytest.mark.parametrize(
    "value",
    ["3530111333300000", "30569309025904", "6200000000000005", "4111.1111.1111.1111"],
    ids=["JCB", "Diners", "UnionPay", "dots"],
)
def test_more_card_brands_and_dot_grouping_are_found(value: str) -> None:
    assert _cards(f"tarjeta {value}") == [value]


def test_a_lone_count_before_a_card_does_not_hide_it() -> None:
    assert _cards("serie 2 4111 1111 1111 1111") == ["4111 1111 1111 1111"]
