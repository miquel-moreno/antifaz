"""The key name rule (issue 121, PR 8a, ADR-0020).

1 to 64 printable characters (the length is checked first), no whitespace at either end, and
never a control or invisible character: Unicode categories Cc, Cf, Zl, Zp, Cs, Co and Cn, every
Default_Ignorable_Code_Point and U+2800 (BRAILLE PATTERN BLANK) are refused anywhere. A name is
only a label for the admin; the key is identified by its number. The error never holds the name,
and the library's frames in a traceback do not keep it either.

Characters are written as chr(0x...), never literally: an invisible character in the source is
the very thing this rule is about.
"""

import types
import unicodedata
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

import antifaz.keys
from antifaz.keys.pepper import PepperRing
from antifaz.keys.records import (
    DEFAULT_IGNORABLE,
    MAX_NAME_LENGTH,
    Scope,
    check_name,
)
from antifaz.keys.store import MemoryKeyStore

PACKAGE = Path(antifaz.keys.__file__).resolve().parent
PEPPER = b"test-pepper-" + b"0" * 32
START = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
INVISIBLE = "control or invisible"

# Unicode 15.0.0 DerivedCoreProperties.txt, Default_Ignorable_Code_Point, as in the official
# file: copied here on its own so the test compares two copies, not the code with itself.
OFFICIAL_DEFAULT_IGNORABLE = [
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180D),
    (0x180E, 0x180E),
    (0x180F, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x2064),
    (0x2065, 0x2065),
    (0x2066, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0000),
    (0xE0001, 0xE0001),
    (0xE0002, 0xE001F),
    (0xE0020, 0xE007F),
    (0xE0080, 0xE00FF),
    (0xE0100, 0xE01EF),
    (0xE01F0, 0xE0FFF),
]
EDGES = sorted({point for first, last in OFFICIAL_DEFAULT_IGNORABLE for point in (first, last)})
BRAILLE_PATTERN_BLANK = 0x2800

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
REFUSED = sorted({*BIDI_CONTROLS, *ZERO_WIDTH, *OTHER_NON_PRINTABLE, *EDGES, BRAILLE_PATTERN_BLANK})


def text(*points: int) -> str:
    return "".join(chr(point) for point in points)


def _where(point: int) -> list[str]:
    character = chr(point)
    return [character + "app", "ap" + character + "p", "app" + character]


def _store() -> MemoryKeyStore:
    return MemoryKeyStore(PepperRing({1: PEPPER}, current=1), lambda: START)


# --- the Default_Ignorable_Code_Point table ------------------------------------------------


def test_the_table_is_built_for_the_unicode_version_of_this_python() -> None:
    # A Python upgrade with a newer Unicode makes this fail: review the table against the new
    # DerivedCoreProperties.txt and update the version here and in records.py.
    assert unicodedata.unidata_version == "15.0.0"


def test_the_table_has_exactly_the_4174_official_code_points() -> None:
    official = {
        point for first, last in OFFICIAL_DEFAULT_IGNORABLE for point in range(first, last + 1)
    }

    assert sum(last - first + 1 for first, last in OFFICIAL_DEFAULT_IGNORABLE) == 4174
    assert len(DEFAULT_IGNORABLE) == 4174
    assert frozenset(official) == DEFAULT_IGNORABLE


# --- refused characters ----------------------------------------------------------------------


@pytest.mark.parametrize("name", [name for point in REFUSED for name in _where(point)], ids=ascii)
def test_a_control_or_invisible_character_is_refused_at_the_start_middle_and_end(
    name: str,
) -> None:
    with pytest.raises(ValueError, match=INVISIBLE) as caught:
        check_name(name)

    assert name not in str(caught.value)
    assert name not in repr(caught.value)
    assert caught.value.__context__ is None


@pytest.mark.parametrize("point", REFUSED, ids=hex)
async def test_the_store_refuses_the_name_at_the_start_middle_and_end_and_keeps_nothing(
    point: int,
) -> None:
    store = _store()

    for name in _where(point):
        with pytest.raises(ValueError, match=INVISIBLE):
            await store.create(name, {Scope.SCAN})

    assert await store.list() == ()


def test_the_braille_pattern_blank_is_refused() -> None:
    with pytest.raises(ValueError, match=INVISIBLE):
        check_name("app" + chr(BRAILLE_PATTERN_BLANK) + "x")


@pytest.mark.parametrize(
    "name",
    [" app", "app ", chr(0x09) + "app", "app" + chr(0x0A), chr(0xA0) + "app", "app" + chr(0x3000)],
    ids=ascii,
)
def test_whitespace_at_either_end_is_refused(name: str) -> None:
    with pytest.raises(ValueError) as caught:
        check_name(name)

    assert name not in str(caught.value)


@pytest.mark.parametrize("name", ["", "a" * (MAX_NAME_LENGTH + 1)])
def test_an_empty_or_too_long_name_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match="1 to 64"):
        check_name(name)


def test_the_length_is_checked_before_the_characters() -> None:
    too_long = chr(0x202E) + "a" * MAX_NAME_LENGTH

    with pytest.raises(ValueError, match="1 to 64"):
        check_name(too_long)


@pytest.mark.parametrize(
    "name",
    [
        "a",
        "a" * MAX_NAME_LENGTH,
        "Billing bot " + chr(0xB7) + " ES",
        "factura" + text(0xE7, 0xF3) + "-2026",
        text(0xE9) + "quipe/ventes",
        text(0x041A, 0x043B, 0x044E, 0x0447),  # Cyrillic
        text(0x039A, 0x03BB, 0x03B5, 0x03B9, 0x03B4, 0x03AF),  # Greek
        text(0x0645, 0x0641, 0x062A, 0x0627, 0x062D),  # Arabic: letters, no controls
        text(0x65E5, 0x672C, 0x8A9E, 0x306E, 0x30A2, 0x30D7, 0x30EA),  # Japanese
        "app " + chr(0x1F600),  # an emoji without U+FE0F
        "team " + chr(0x1F680) + " ops",
    ],
    ids=ascii,
)
def test_printable_names_in_any_script_are_accepted(name: str) -> None:
    check_name(name)


# --- the rejected name is not kept in the library's frames ---------------------------------


CANARY_NAME = "canary-name-" + chr(0x202E) + "-hidden"


def _keys_frames(error: BaseException) -> Iterator[types.FrameType]:
    traceback = error.__traceback__
    while traceback is not None:
        if Path(traceback.tb_frame.f_code.co_filename).resolve().parent == PACKAGE:
            yield traceback.tb_frame
        traceback = traceback.tb_next


def _holds(frame: types.FrameType, secret: str) -> bool:
    return any(
        value is secret or (isinstance(value, str) and secret in value)
        for value in frame.f_locals.values()
    )


def test_check_name_does_not_keep_the_rejected_name_in_its_frame() -> None:
    with pytest.raises(ValueError) as caught:
        check_name(CANARY_NAME)

    frames = list(_keys_frames(caught.value))
    assert frames
    assert not any(_holds(frame, CANARY_NAME) for frame in frames)


async def test_create_does_not_keep_the_rejected_name_in_its_frames() -> None:
    store = _store()

    with pytest.raises(ValueError) as caught:
        await store.create(CANARY_NAME, {Scope.SCAN})

    frames = list(_keys_frames(caught.value))
    assert frames
    assert not any(_holds(frame, CANARY_NAME) for frame in frames)
