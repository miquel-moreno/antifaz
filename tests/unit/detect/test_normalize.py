"""Detector normalisation (ADR-0014): folded view of the text and the map back to offsets."""

import pytest

from antifaz import mask, restore
from antifaz.detect.normalize import HOMOGLYPHS, normalize
from antifaz.detect.scan import scan
from antifaz.detect.types import EntityType as T

ZWSP, SHY, NBSP, BOM, WJ = "\u200b", "\u00ad", "\u00a0", "\ufeff", "\u2060"


def test_plain_ascii_is_returned_as_it_is() -> None:
    text = "DNI 12345678Z"
    assert normalize(text).text is text


@pytest.mark.parametrize(
    ("original", "folded"),
    [
        ("\uff11\uff12\uff3a", "12Z"),  # full-width digits and letter
        ("a" + ZWSP + "b" + SHY + "c" + BOM + WJ, "abc"),  # invisible characters dropped
        ("1\t2" + NBSP + "3\u2009 4", "1 2 3  4"),  # odd spaces become a space
        ("\u0425\u0415\u0417\u0396\u03bf", "XEZZo"),  # Cyrillic and Greek look-alikes
        ("3.\u00ba 2.\u00aa", "3.\u00ba 2.\u00aa"),  # ordinal indicators stay (addresses)
        ("l\u00ednea\n", "l\u00ednea\n"),  # accents and line breaks stay
    ],
)
def test_folding(original: str, folded: str) -> None:
    assert normalize(original).text == folded


def test_every_homoglyph_maps_to_one_ascii_letter() -> None:
    assert all(len(latin) == 1 and latin.isascii() for latin in HOMOGLYPHS.values())


def test_offsets_map_back_over_dropped_characters() -> None:
    original = ZWSP + "ab" + ZWSP + ZWSP + "cd" + ZWSP
    view = normalize(original)
    assert view.text == "abcd"
    assert view.original_start(0) == 1
    assert view.original_start(2) == 5  # "c", after the two dropped characters
    assert view.original_end(4) == 7  # after "d": the trailing invisible one stays outside
    assert original[view.original_start(1) : view.original_end(3)] == "b" + ZWSP * 2 + "c"


FULLWIDTH = "\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18Z"
SPACED = "12" + NBSP + "345" + NBSP + "678" + NBSP + "Z"


@pytest.mark.parametrize(
    ("prefix", "value", "entity_type"),
    [
        ("DNI ", "1234" + ZWSP + "5678Z", T.ES_DNI),
        ("DNI ", "1234" + SHY + "5678Z", T.ES_DNI),
        ("DNI ", SPACED, T.ES_DNI),
        ("DNI ", FULLWIDTH, T.ES_DNI),
        ("DNI ", "12345678\u0417", T.ES_DNI),  # Cyrillic Ze as the letter
        ("NIE ", "\u04251234567L", T.ES_NIE),  # Cyrillic Ha as the X
        ("", "\u0415S9121000418450200051332", T.IBAN),  # Cyrillic Ie as the E
    ],
)
def test_scan_covers_the_original_characters(prefix: str, value: str, entity_type: T) -> None:
    text = prefix + value
    assert [(text[s.start : s.end], s.type) for s in scan(text)] == [(value, entity_type)]
    result = mask(text)
    assert restore(result.text, result.vault) == text


@pytest.mark.parametrize(
    ("text", "value", "entity_type"),
    [
        ("DNI 12 345 678 Z", "12 345 678 Z", T.ES_DNI),
        ("DNI 1234\n5678Z", "1234\n5678Z", T.ES_DNI),
        ("DNI 12345678\r\nZ", "12345678\r\nZ", T.ES_DNI),
        ("NIE X 1 234 567 L", "X 1 234 567 L", T.ES_NIE),
        ("NIEX1234567L", "X1234567L", T.ES_NIE),
        ("ES91 2100 0418\n4502 0005 1332", "ES91 2100 0418\n4502 0005 1332", T.IBAN),
    ],
)
def test_new_separators_and_glued_values(text: str, value: str, entity_type: T) -> None:
    assert [(text[s.start : s.end], s.type) for s in scan(text)] == [(value, entity_type)]


@pytest.mark.parametrize(
    "text",
    [
        "DNI 1234\n\n5678Z",  # two line breaks: two separate numbers
        "12 345 678  Z",  # two spaces before the letter
        "tengo 12345678 casas",  # the next word is not the control letter
    ],
)
def test_strict_counts_keep_false_positives_out(text: str) -> None:
    assert scan(text) == []
