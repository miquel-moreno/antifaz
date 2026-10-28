"""The virtual key format (ADR-0020): `afz_` + 43 random base62 characters + a 6-character
base62 checksum. 53 characters, always.

- The body is 43 characters drawn one by one with `secrets.choice` from the 62 letters and
  digits: about 256 bits, unbiased.
- The checksum is the CRC32 of the prefix and the body (ASCII), written in base62 with a fixed
  width of 6 (62**6 > 2**32), most significant digit first. It is ONLY a filter for typos and
  made-up keys, so they are refused without touching a store: it is not security. Anyone can
  compute a valid checksum; what protects a key is its 256 random bits and the HMAC (pepper.py).
- CRC32 detects every change of one byte, so a key with one character changed always fails
  here (a property test checks it), and so does one with a character more or less (length).

parse_key() checks, in this order and in linear time, without regular expressions: the type
(exactly str), the length, the prefix, the alphabet (ASCII letters and digits only, so no
spaces, no non-ASCII look-alikes) and the checksum. Anything else raises MalformedKeyError with
a fixed message: never the text, never a cause or context.

Forbidden: putting the text in an error or a log. Accepting a key with surrounding spaces "to
be kind": the door gets the exact header value.
"""

import hmac
import secrets
import zlib
from typing import TypeGuard

PREFIX = "afz_"
BODY_LENGTH = 43
CHECKSUM_LENGTH = 6
KEY_LENGTH = len(PREFIX) + BODY_LENGTH + CHECKSUM_LENGTH
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_ALPHABET_SET = frozenset(ALPHABET)
_BASE = len(ALPHABET)


class MalformedKeyError(ValueError):
    """A text that is not a well-formed virtual key. The message is always the same."""

    def __init__(self) -> None:
        super().__init__("malformed key")


def _checksum(head: str) -> str:
    """CRC32 of `head` (prefix and body, ASCII) in fixed-width base62."""
    number = zlib.crc32(head.encode("ascii"))
    digits = []
    for _ in range(CHECKSUM_LENGTH):
        number, digit = divmod(number, _BASE)
        digits.append(ALPHABET[digit])
    return "".join(reversed(digits))


def _is_base62(text: str) -> bool:
    return all(character in _ALPHABET_SET for character in text)


def key_from_body(body: str) -> str:
    """The key for a body of 43 base62 characters (prefix, body and checksum)."""
    if type(body) is not str or len(body) != BODY_LENGTH or not _is_base62(body):
        del body
        raise MalformedKeyError from None
    return PREFIX + body + _checksum(PREFIX + body)


def generate_key() -> str:
    """A new random key. The caller shows it once and keeps only its hash."""
    return key_from_body("".join(secrets.choice(ALPHABET) for _ in range(BODY_LENGTH)))


def _is_well_formed(text: object) -> TypeGuard[str]:
    if type(text) is not str or len(text) != KEY_LENGTH or not text.startswith(PREFIX):
        return False
    rest = text[len(PREFIX) :]
    if not _is_base62(rest):
        return False
    head, checksum = text[:-CHECKSUM_LENGTH], text[-CHECKSUM_LENGTH:]
    return hmac.compare_digest(_checksum(head), checksum)


def parse_key(text: object) -> str:
    """`text` itself if it is a well-formed key; MalformedKeyError otherwise.

    Well-formed is not valid: only a store says whether the key exists, is revoked or expired.
    """
    if not _is_well_formed(text):
        del text  # the traceback keeps this frame: it must not keep the text
        raise MalformedKeyError from None
    return text


__all__ = [
    "ALPHABET",
    "BODY_LENGTH",
    "CHECKSUM_LENGTH",
    "KEY_LENGTH",
    "PREFIX",
    "MalformedKeyError",
    "generate_key",
    "key_from_body",
    "parse_key",
]
