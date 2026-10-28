"""The pepper ring (ADR-0020): HMAC-SHA256(pepper, key), with a version for every pepper.

A store keeps, for every key, HMAC-SHA256 of the key with a secret kept OUTSIDE the store (the
pepper) and the version of the pepper it used. A copy of the store alone does not let anyone
test guesses, and a stored hash is never a credential: verifying always starts from the clear
key. Keys are 256 random bits, so a fast keyed hash is enough (no argon2: a slow hash would let
anyone make the gateway burn CPU on every request with made-up keys).

Rotation: a ring holds several versions and says which one is CURRENT (used for new keys).
Keys made with an older version keep working while that version stays in the ring; dropping a
version makes its keys unknown. Losing every pepper makes every virtual key stop working.

Rules: versions are positive integers; every pepper is `bytes` of at least MIN_PEPPER_BYTES and
different from the others; the ring keeps its own copy. The pepper never appears in a repr, a
str or an error, and the ring cannot be pickled or copied.

Forbidden: logging a pepper, a key or a digest. Hashing without a pepper.
"""

import hashlib
import hmac
from collections.abc import Mapping
from typing import NoReturn

MIN_PEPPER_BYTES = 32


def _is_version(value: object) -> bool:
    return type(value) is int and value >= 1


class PepperRing:
    """Pepper versions -> peppers, and the current version."""

    __slots__ = ("_current", "_peppers")

    def __init__(self, peppers: Mapping[int, bytes], current: int) -> None:
        if not isinstance(peppers, Mapping) or not peppers:
            raise ValueError("the key pepper ring needs at least one pepper")
        copied: dict[int, bytes] = {}
        for version, pepper in peppers.items():
            if not _is_version(version):
                raise ValueError("a key pepper version must be a positive integer")
            if type(pepper) is not bytes:
                raise TypeError("a key pepper must be bytes")
            if len(pepper) < MIN_PEPPER_BYTES:
                raise ValueError(f"a key pepper must be at least {MIN_PEPPER_BYTES} bytes")
            if any(hmac.compare_digest(pepper, other) for other in copied.values()):
                raise ValueError("two key pepper versions have the same pepper")
            copied[version] = pepper
        if not _is_version(current) or current not in copied:
            raise ValueError("the current key pepper version is not in the ring")
        self._peppers = dict(sorted(copied.items()))
        self._current = current

    @property
    def current(self) -> int:
        """The version used for new keys."""
        return self._current

    @property
    def versions(self) -> tuple[int, ...]:
        """Every version in the ring, in increasing order."""
        return tuple(self._peppers)

    def digest(self, key: str, version: int) -> bytes:
        """HMAC-SHA256 of the key (ASCII, already parsed) with the pepper of `version`."""
        pepper = self._peppers.get(version) if _is_version(version) else None
        if pepper is None:
            raise LookupError("no key pepper with that version")
        if type(key) is not str or not key.isascii():
            # Never let encode() fail: a UnicodeEncodeError would carry the whole text.
            del key
            raise ValueError("only a parsed key can be hashed") from None
        return hmac.new(pepper, key.encode("ascii"), hashlib.sha256).digest()

    def __repr__(self) -> str:
        return f"PepperRing(versions={self.versions}, current={self._current})"

    __str__ = __repr__

    def _refuse(self, *_: object) -> NoReturn:
        raise TypeError("PepperRing cannot be copied or serialised")

    __reduce__ = _refuse
    __reduce_ex__ = _refuse
    __getstate__ = _refuse
    __copy__ = _refuse
    __deepcopy__ = _refuse


__all__ = ["MIN_PEPPER_BYTES", "PepperRing"]
