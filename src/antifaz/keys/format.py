"""Key format (stub)."""

PREFIX = "afz_"
BODY_LENGTH = 43
CHECKSUM_LENGTH = 6
KEY_LENGTH = len(PREFIX) + BODY_LENGTH + CHECKSUM_LENGTH
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


class MalformedKeyError(ValueError):
    """Stub."""


def key_from_body(body: str) -> str:
    raise NotImplementedError


def generate_key() -> str:
    raise NotImplementedError


def parse_key(text: object) -> str:
    raise NotImplementedError
