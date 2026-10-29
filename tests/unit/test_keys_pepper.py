"""The pepper ring (issue 121, PR 8a, ADR-0020): HMAC-SHA256 with a versioned pepper."""

import copy
import hashlib
import hmac
import pickle

import pytest

from antifaz.keys.format import BODY_LENGTH
from antifaz.keys.pepper import MIN_PEPPER_BYTES, PepperRing

# Fake peppers, written here: never a real one.
PEPPER_1 = b"test-pepper-" + b"1" * 32
PEPPER_2 = b"test-pepper-" + b"2" * 32
KEY = "afz_" + "K" * BODY_LENGTH + "000000"  # digest() hashes any text it is given


def test_the_digest_is_hmac_sha256_of_the_key_with_the_pepper_of_that_version() -> None:
    ring = PepperRing({1: PEPPER_1, 2: PEPPER_2}, current=2)

    assert ring.digest(KEY, 1) == hmac.new(PEPPER_1, KEY.encode("ascii"), hashlib.sha256).digest()
    assert ring.digest(KEY, 2) == hmac.new(PEPPER_2, KEY.encode("ascii"), hashlib.sha256).digest()
    assert ring.current == 2
    assert ring.versions == (1, 2)


def test_the_same_version_gives_the_same_digest_in_another_ring() -> None:
    old = PepperRing({1: PEPPER_1}, current=1)
    rotated = PepperRing({2: PEPPER_2, 1: PEPPER_1}, current=2)

    assert old.digest(KEY, 1) == rotated.digest(KEY, 1)
    assert rotated.versions == (1, 2)


def test_a_version_that_is_not_in_the_ring_is_refused() -> None:
    ring = PepperRing({1: PEPPER_1}, current=1)

    with pytest.raises(LookupError):
        ring.digest(KEY, 2)


def test_the_minimum_pepper_length_is_32_bytes() -> None:
    assert MIN_PEPPER_BYTES == 32
    PepperRing({1: b"p" * 32}, current=1)
    with pytest.raises(ValueError, match="32 bytes"):
        PepperRing({1: b"p" * 31}, current=1)


@pytest.mark.parametrize(
    ("peppers", "current"),
    [
        ({}, 1),
        ({1: PEPPER_1}, 2),
        ({0: PEPPER_1}, 0),
        ({-1: PEPPER_1}, -1),
        ({True: PEPPER_1}, True),
        ({1: PEPPER_1.decode()}, 1),
        ({1: bytearray(PEPPER_1)}, 1),
        ({1: PEPPER_1, 2: PEPPER_1}, 2),
    ],
)
def test_a_bad_ring_is_refused(peppers: dict[object, object], current: object) -> None:
    with pytest.raises((ValueError, TypeError)) as caught:
        PepperRing(peppers, current)  # type: ignore[arg-type]
    assert PEPPER_1.decode() not in str(caught.value)


def test_the_ring_never_shows_a_pepper() -> None:
    ring = PepperRing({1: PEPPER_1, 2: PEPPER_2}, current=2)

    for text in (repr(ring), str(ring), f"{ring!r}", "%r" % (ring,), "%s" % (ring,)):  # noqa: UP031
        assert "test-pepper" not in text
        assert PEPPER_1.hex() not in text
    assert repr(ring) == "PepperRing(versions=(1, 2), current=2)"


def test_the_ring_keeps_its_own_copy_of_the_mapping() -> None:
    peppers = {1: PEPPER_1}
    ring = PepperRing(peppers, current=1)
    peppers[1] = PEPPER_2

    assert ring.digest(KEY, 1) == hmac.new(PEPPER_1, KEY.encode(), hashlib.sha256).digest()


def test_the_ring_cannot_be_pickled_or_copied() -> None:
    ring = PepperRing({1: PEPPER_1}, current=1)

    for action in (pickle.dumps, copy.copy, copy.deepcopy):
        with pytest.raises(TypeError):
            action(ring)


def test_the_ring_has_no_attribute_dictionary() -> None:
    ring = PepperRing({1: PEPPER_1}, current=1)

    assert not hasattr(ring, "__dict__")
    with pytest.raises(AttributeError):
        ring.extra = 1  # type: ignore[attr-defined]


@pytest.mark.parametrize("text", ["afz_ñ" + "K" * 48, b"afz_", 7])
def test_digest_refuses_what_is_not_ascii_text_without_echoing_it(text: object) -> None:
    ring = PepperRing({1: PEPPER_1}, current=1)

    with pytest.raises(ValueError) as caught:
        ring.digest(text, 1)  # type: ignore[arg-type]

    assert not isinstance(caught.value, UnicodeError)
    assert str(caught.value) == "only a parsed key can be hashed"
    assert caught.value.__context__ is None


def test_a_ring_needs_a_mapping() -> None:
    with pytest.raises(ValueError):
        PepperRing([(1, PEPPER_1)], current=1)  # type: ignore[arg-type]
