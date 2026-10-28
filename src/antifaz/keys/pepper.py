"""Pepper ring (stub)."""

from collections.abc import Mapping

MIN_PEPPER_BYTES = 32


class PepperRing:
    def __init__(self, peppers: Mapping[int, bytes], current: int) -> None:
        raise NotImplementedError

    @property
    def current(self) -> int:
        raise NotImplementedError

    @property
    def versions(self) -> tuple[int, ...]:
        raise NotImplementedError

    def digest(self, key: str, version: int) -> bytes:
        raise NotImplementedError
