"""Key stores (ADR-0020): the KeyStore protocol and MemoryKeyStore. Standard library only.

KeyStore is what the door (8d) and the CLI (8c) use; MemoryKeyStore implements it here and the
PostgreSQL store (8b) will implement it too. Every method is a coroutine: the PostgreSQL store
talks to the database with asyncpg (ADR-0001) and the door is ASGI, so a lookup must never block
the event loop. MemoryKeyStore never awaits anything inside a call; the pure pieces (format,
pepper digest, record checks) stay synchronous.

- create(name, scopes, ...) -> CreatedKey: a new random key, shown ONCE (CreatedKey.key); the
  store keeps only its record (HMAC with the CURRENT pepper version, never the key).
- get(id), list() -> records in id order; revoke(id) -> the revoked record (the first time is
  kept; None for an id that does not exist). Nothing is ever deleted, ids are never reused.
- verify(presented) -> Verified(Caller) or Refused(reason). It never raises for what it is
  given: anything that is not a well-formed key is MALFORMED before any lookup. A well-formed
  key is hashed with every pepper version in the ring and looked up by (version, hash); not
  found is UNKNOWN, then REVOKED (any revoked_at at all: revocation fails closed, whatever the
  clock says, also for a loaded record revoked "in the future"), then EXPIRED
  (now >= expires_at). The clock is read before the lookup, so a broken clock fails the same
  way for every key. get() and revoke() take only real positive ints (None for anything else).

The clear key is not kept: not in the store, a record, a Caller or an exception. When a call
fails half-way (a broken clock, MemoryError...), the frames of this package in the traceback
are cleared and the key's local is deleted before the error leaves, so the traceback does not
hold it either.

The clock is injected (default: datetime.now(UTC)) and must return aware datetimes; any other
zone is converted to UTC, a naive time is refused.

Concurrency: MemoryKeyStore has no lock. It is for tests and for one process on one thread
(an event loop); the PostgreSQL store is the one for real use.

Forbidden: keeping the clear key in any attribute. Accepting a stored hash as a credential.
A cache of keys by prefix.
"""

import hmac
import traceback
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import NoReturn, Protocol, runtime_checkable

from antifaz.keys.format import MalformedKeyError, generate_key, parse_key
from antifaz.keys.pepper import PepperRing
from antifaz.keys.records import (
    DEFAULT_LIFETIME,
    Caller,
    CreatedKey,
    Expiry,
    KeyRecord,
    RefusalReason,
    Refused,
    Scope,
    Verified,
    VerifyResult,
    check_limit,
    check_name,
    check_scopes,
    is_positive_int,
)

type Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


@runtime_checkable
class KeyStore(Protocol):
    async def create(
        self,
        name: str,
        scopes: Iterable[Scope],
        *,
        expires_in: timedelta | Expiry = DEFAULT_LIFETIME,
        per_minute_limit: int | None = None,
        per_day_limit: int | None = None,
    ) -> CreatedKey: ...

    async def get(self, key_id: int) -> KeyRecord | None: ...

    async def list(self) -> tuple[KeyRecord, ...]: ...

    async def revoke(self, key_id: int) -> KeyRecord | None: ...

    async def verify(self, presented: object) -> VerifyResult: ...


def _scope_set(scopes: object) -> frozenset[Scope]:
    if isinstance(scopes, str | bytes) or not isinstance(scopes, Iterable):
        raise TypeError("key scopes must be a collection of Scope")
    given = tuple(scopes)
    # Before the set: "scan" == Scope.SCAN, so a set could silently keep either one.
    if not all(type(scope) is Scope for scope in given):
        raise TypeError("every key scope must be a Scope")
    chosen = frozenset(given)
    check_scopes(chosen)
    return chosen


def _expires_at(now: datetime, expires_in: object) -> datetime | None:
    if expires_in is Expiry.NEVER:
        return None
    if type(expires_in) is not timedelta:
        raise TypeError("expires_in must be a timedelta or Expiry.NEVER")
    if expires_in <= timedelta(0):
        raise ValueError("expires_in must be positive")
    return now + expires_in


def _forget_frames(error: BaseException) -> None:
    """Clear the locals of every finished frame of the traceback (a key may be one)."""
    traceback.clear_frames(error.__traceback__)


class MemoryKeyStore:
    """KeyStore in memory. `records` loads existing records (a restart, a pepper rotation)."""

    __slots__ = ("_by_hash", "_clock", "_next_id", "_peppers", "_records")

    def __init__(
        self, peppers: PepperRing, clock: Clock = utc_now, records: Iterable[KeyRecord] = ()
    ) -> None:
        if type(peppers) is not PepperRing:
            raise TypeError("peppers must be a PepperRing")
        self._peppers = peppers
        self._clock = clock
        self._records: dict[int, KeyRecord] = {}
        self._by_hash: dict[tuple[int, bytes], int] = {}
        for record in records:
            if type(record) is not KeyRecord:
                raise TypeError("only KeyRecord objects can be loaded")
            self._add(record)
        self._next_id = max(self._records, default=0) + 1

    def _add(self, record: KeyRecord) -> None:
        index = (record.pepper_version, record.key_hash)
        if record.id in self._records or index in self._by_hash:
            raise ValueError("two key records with the same id or hash")
        self._records[record.id] = record
        self._by_hash[index] = record.id

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise ValueError("the key store clock must return an aware datetime (UTC)")
        return now.astimezone(UTC)

    async def create(
        self,
        name: str,
        scopes: Iterable[Scope],
        *,
        expires_in: timedelta | Expiry = DEFAULT_LIFETIME,
        per_minute_limit: int | None = None,
        per_day_limit: int | None = None,
    ) -> CreatedKey:
        # Everything that can be refused is checked before the key exists.
        check_name(name)
        chosen = _scope_set(scopes)
        check_limit(per_minute_limit)
        check_limit(per_day_limit)
        now = self._now()
        expires_at = _expires_at(now, expires_in)
        version = self._peppers.current
        key = generate_key()
        try:
            record = KeyRecord(
                id=self._next_id,
                name=name,
                scopes=chosen,
                created_at=now,
                expires_at=expires_at,
                revoked_at=None,
                key_hash=self._peppers.digest(key, version),
                pepper_version=version,
                per_minute_limit=per_minute_limit,
                per_day_limit=per_day_limit,
            )
            self._add(record)
        except BaseException as error:
            _forget_frames(error)
            del key  # this frame stays in the traceback: it must not keep the key
            raise
        self._next_id += 1
        return CreatedKey(key, record)

    def _record_of(self, key_id: object) -> KeyRecord | None:
        """The record with that number; None for anything but a real positive int (True,
        1.0 or "1" are not key numbers, even if they compare equal to one)."""
        return self._records.get(key_id) if is_positive_int(key_id) else None

    async def get(self, key_id: int) -> KeyRecord | None:
        return self._record_of(key_id)

    async def list(self) -> tuple[KeyRecord, ...]:
        return tuple(self._records[key_id] for key_id in sorted(self._records))

    async def revoke(self, key_id: int) -> KeyRecord | None:
        record = self._record_of(key_id)
        if record is None or record.revoked_at is not None:
            return record
        revoked = KeyRecord(
            id=record.id,
            name=record.name,
            scopes=record.scopes,
            created_at=record.created_at,
            expires_at=record.expires_at,
            revoked_at=max(self._now(), record.created_at),
            key_hash=record.key_hash,
            pepper_version=record.pepper_version,
            per_minute_limit=record.per_minute_limit,
            per_day_limit=record.per_day_limit,
        )
        self._records[key_id] = revoked
        return revoked

    def _find(self, presented: object) -> KeyRecord | RefusalReason | None:
        """The record of a well-formed key, None if unknown, MALFORMED if not a key."""
        try:
            key = parse_key(presented)
        except MalformedKeyError:
            return RefusalReason.MALFORMED
        for version in self._peppers.versions:
            digest = self._peppers.digest(key, version)
            key_id = self._by_hash.get((version, digest))
            if key_id is not None:
                record = self._records[key_id]
                if hmac.compare_digest(record.key_hash, digest):
                    return record
        return None

    async def verify(self, presented: object) -> VerifyResult:
        try:
            # The clock first: a broken clock fails the same for every key, known or not.
            now = self._now()
            found = self._find(presented)
        except BaseException as error:
            _forget_frames(error)
            raise
        finally:
            del presented  # from here on, nothing here holds the key
        if isinstance(found, RefusalReason):
            return Refused(found)
        if found is None:
            return Refused(RefusalReason.UNKNOWN)
        if found.revoked_at is not None:  # fails closed: whatever the clock says
            return Refused(RefusalReason.REVOKED)
        if found.expires_at is not None and now >= found.expires_at:
            return Refused(RefusalReason.EXPIRED)
        return Verified(Caller(key_id=found.id, scopes=found.scopes))

    def __repr__(self) -> str:
        return f"MemoryKeyStore(keys={len(self._records)})"

    __str__ = __repr__

    def _refuse(self, *_: object) -> NoReturn:
        raise TypeError("MemoryKeyStore cannot be copied or serialised")

    __reduce__ = _refuse
    __reduce_ex__ = _refuse
    __getstate__ = _refuse
    __copy__ = _refuse
    __deepcopy__ = _refuse


__all__ = ["Clock", "KeyStore", "MemoryKeyStore", "utc_now"]
