"""Virtual key format (issue 121, PR 8a, ADR-0020): `afz_` + 43 base62 + 6 base62 checksum."""

import zlib

import pytest

from antifaz.keys.format import (
    ALPHABET,
    BODY_LENGTH,
    CHECKSUM_LENGTH,
    KEY_LENGTH,
    PREFIX,
    MalformedKeyError,
    generate_key,
    key_from_body,
    parse_key,
)

BODY = "A" * BODY_LENGTH


def _base62(number: int, width: int) -> str:
    digits = []
    for _ in range(width):
        number, digit = divmod(number, 62)
        digits.append(ALPHABET[digit])
    return "".join(reversed(digits))


def test_the_alphabet_is_base62_digits_then_upper_then_lower() -> None:
    assert ALPHABET == "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    assert KEY_LENGTH == 4 + 43 + 6


def test_a_generated_key_has_the_prefix_the_body_and_the_checksum() -> None:
    key = generate_key()

    assert len(key) == KEY_LENGTH
    assert key.startswith(PREFIX)
    assert set(key[len(PREFIX) :]) <= set(ALPHABET)
    assert parse_key(key) == key


def test_generated_keys_are_different_every_time() -> None:
    keys = {generate_key() for _ in range(200)}

    assert len(keys) == 200


def test_generated_keys_use_the_whole_alphabet() -> None:
    seen = set("".join(generate_key()[len(PREFIX) : len(PREFIX) + BODY_LENGTH] for _ in range(200)))

    assert seen == set(ALPHABET)


def test_the_checksum_is_crc32_of_prefix_and_body_in_fixed_width_base62() -> None:
    key = key_from_body(BODY)

    expected = _base62(zlib.crc32((PREFIX + BODY).encode("ascii")), CHECKSUM_LENGTH)
    assert key == PREFIX + BODY + expected


def test_a_small_checksum_keeps_its_leading_zeros() -> None:
    # Search a body whose CRC32 is below 62**5: its checksum starts with "0".
    for number in range(100_000):
        body = f"{number:0>{BODY_LENGTH}}"
        if zlib.crc32((PREFIX + body).encode("ascii")) < 62**5:
            key = key_from_body(body)
            assert len(key) == KEY_LENGTH
            assert key[-CHECKSUM_LENGTH] == "0"
            assert parse_key(key) == key
            return
    pytest.fail("no small checksum found")


@pytest.mark.parametrize("body", ["", "A" * 42, "A" * 44, "A" * 42 + "-", "A" * 42 + "ñ"])
def test_key_from_body_refuses_a_body_that_is_not_43_base62_characters(body: str) -> None:
    with pytest.raises(MalformedKeyError):
        key_from_body(body)


def _refused(text: object) -> None:
    with pytest.raises(MalformedKeyError) as caught:
        parse_key(text)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def _good() -> str:
    return key_from_body(BODY)


@pytest.mark.parametrize(
    "change",
    [
        lambda key: "AFZ_" + key[4:],  # prefix case
        lambda key: "afz-" + key[4:],
        lambda key: "sk-" + key[4:],
        lambda key: key[:-1],  # too short
        lambda key: key + "A",  # too long
        lambda key: " " + key,
        lambda key: key + "\n",
        lambda key: key.strip() + " ",
        lambda key: "\t" + key[1:],
        lambda key: key[:10] + "-" + key[11:],  # outside the alphabet
        lambda key: key[:10] + "_" + key[11:],
        lambda key: key[:10] + "é" + key[11:],  # non-ASCII
        lambda key: key[:10] + "Ff21" + key[11:],  # full-width A
        lambda key: key[:10] + "\x00" + key[11:],
        lambda key: key[:-1] + ("B" if key[-1] != "B" else "C"),  # wrong checksum
        lambda key: key[:20] + ("B" if key[20] != "B" else "C") + key[21:],  # body typo
        lambda key: "",
        lambda key: PREFIX,
        lambda key: key * 2,
    ],
)
def test_parse_refuses_every_malformed_key(change: object) -> None:
    assert callable(change)
    _refused(change(_good()))


@pytest.mark.parametrize("value", [None, 42, b"afz_", ["afz_"], object()])
def test_parse_refuses_values_that_are_not_text(value: object) -> None:
    _refused(value)


def test_parse_refuses_the_good_key_as_bytes() -> None:
    _refused(_good().encode("ascii"))


def test_parse_refuses_a_str_subclass() -> None:
    class Sneaky(str):
        pass

    _refused(Sneaky(_good()))


def test_parse_refuses_a_huge_text_without_reading_it_all() -> None:
    _refused("afz_" + "A" * 10_000_000)


def test_the_error_message_is_fixed_and_never_holds_the_text() -> None:
    key = _good()
    typo = key[:-1] + ("B" if key[-1] != "B" else "C")

    with pytest.raises(MalformedKeyError) as caught:
        parse_key(typo)

    assert str(caught.value) == "malformed key"
    assert caught.value.args == ("malformed key",)
    assert typo not in repr(caught.value)


def test_the_parse_frame_does_not_keep_the_refused_text() -> None:
    key = _good()
    typo = key[:-1] + ("B" if key[-1] != "B" else "C")

    with pytest.raises(MalformedKeyError) as caught:
        parse_key(typo)

    traceback = caught.value.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_code.co_filename.endswith("format.py"):
            assert all(value is not typo for value in frame.f_locals.values())
        traceback = traceback.tb_next
