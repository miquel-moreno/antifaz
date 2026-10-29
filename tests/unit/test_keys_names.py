"""The key name rule (issue 121, PR 8a, ADR-0020).

1 to 64 printable characters, no whitespace at either end, and never a control or invisible
format character: Unicode categories Cc, Cf, Zl, Zp, Cs, Co and Cn are refused anywhere. A name
is shown to the admin (keys list, the panel later): a bidi control could make it read as
another name, a zero-width one could make two names look the same. The error never holds the
name.
"""

from datetime import UTC, datetime

import pytest

from antifaz.keys.pepper import PepperRing
from antifaz.keys.records import (
    FORBIDDEN_NAME_CATEGORIES,
    MAX_NAME_LENGTH,
    Scope,
    check_name,
)
from antifaz.keys.store import MemoryKeyStore

PEPPER = b"test-pepper-" + b"0" * 32
START = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)

# Code points, never the characters themselves: an invisible character in the source is the
# very thing this rule is about.
BIDI_CONTROLS = [
    0x202A,  # LEFT-TO-RIGHT EMBEDDING
    0x202B,  # RIGHT-TO-LEFT EMBEDDING
    0x202C,  # POP DIRECTIONAL FORMATTING
    0x202D,  # LEFT-TO-RIGHT OVERRIDE
    0x202E,  # RIGHT-TO-LEFT OVERRIDE
    0x2066,  # LEFT-TO-RIGHT ISOLATE
    0x2067,  # RIGHT-TO-LEFT ISOLATE
    0x2068,  # FIRST STRONG ISOLATE
    0x2069,  # POP DIRECTIONAL ISOLATE
    0x200E,  # LEFT-TO-RIGHT MARK
    0x200F,  # RIGHT-TO-LEFT MARK
    0x061C,  # ARABIC LETTER MARK
]
ZERO_WIDTH = [
    0x200B,  # ZERO WIDTH SPACE
    0x200C,  # ZERO WIDTH NON-JOINER
    0x200D,  # ZERO WIDTH JOINER
    0x2060,  # WORD JOINER
    0xFEFF,  # ZERO WIDTH NO-BREAK SPACE (BOM)
]
OTHER_NON_PRINTABLE = [
    0x00,  # Cc
    0x07,  # Cc
    0x7F,  # Cc
    0x85,  # Cc (NEXT LINE)
    0xAD,  # Cf (SOFT HYPHEN)
    0x2028,  # Zl
    0x2029,  # Zp
    0xD800,  # Cs (a lone surrogate)
    0xE000,  # Co (private use)
    0xF0000,  # Co (supplementary private use)
    0x0378,  # Cn (unassigned)
]
REFUSED = [chr(point) for point in BIDI_CONTROLS + ZERO_WIDTH + OTHER_NON_PRINTABLE]


def _where(character: str) -> list[str]:
    return [character + "app", "ap" + character + "p", "app" + character]


def test_the_forbidden_categories_are_the_control_and_invisible_ones() -> None:
    assert frozenset({"Cc", "Cf", "Zl", "Zp", "Cs", "Co", "Cn"}) == FORBIDDEN_NAME_CATEGORIES


@pytest.mark.parametrize(
    "name", [name for character in REFUSED for name in _where(character)], ids=ascii
)
def test_a_control_or_invisible_character_is_refused_at_the_start_middle_and_end(
    name: str,
) -> None:
    with pytest.raises(ValueError, match="control or invisible") as caught:
        check_name(name)

    assert name not in str(caught.value)
    assert name not in repr(caught.value)
    assert caught.value.__context__ is None


@pytest.mark.parametrize("character", REFUSED, ids=ascii)
async def test_the_store_refuses_the_name_and_keeps_nothing(character: str) -> None:
    store = MemoryKeyStore(PepperRing({1: PEPPER}, current=1), lambda: START)

    with pytest.raises(ValueError, match="control or invisible"):
        await store.create("ap" + character + "p", {Scope.SCAN})

    assert await store.list() == ()


@pytest.mark.parametrize(
    "name",
    [" app", "app ", chr(0x09) + "app", "app" + chr(0x0A), chr(0xA0) + "app", "app" + chr(0x3000)],
)
def test_whitespace_at_either_end_is_refused(name: str) -> None:
    with pytest.raises(ValueError) as caught:
        check_name(name)

    assert name not in str(caught.value)


@pytest.mark.parametrize("name", ["", "a" * (MAX_NAME_LENGTH + 1)])
def test_an_empty_or_too_long_name_is_refused(name: str) -> None:
    with pytest.raises(ValueError):
        check_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "a",
        "a" * MAX_NAME_LENGTH,
        "Billing bot · ES",
        "facturació-2026",
        "équipe/ventes",
        "العربية",  # right-to-left letters are fine, only the controls are not
        "日本語のアプリ",
        "app 😀",
    ],
)
def test_printable_names_in_any_script_are_accepted(name: str) -> None:
    check_name(name)
