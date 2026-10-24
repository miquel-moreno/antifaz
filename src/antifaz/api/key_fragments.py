"""Recognisable pieces of a configured key (invariant 13, issue 76). Signatures only for now."""

from collections.abc import Sequence

FRAGMENT_LENGTH = 10
SHOWN_SUFFIX = 4


def secret_part(key: str) -> str:
    raise NotImplementedError


class KeyFragments:
    def __init__(self, keys: Sequence[str]) -> None:
        raise NotImplementedError

    def found_in(self, text: str) -> bool:
        raise NotImplementedError


__all__ = ["FRAGMENT_LENGTH", "SHOWN_SUFFIX", "KeyFragments", "secret_part"]
