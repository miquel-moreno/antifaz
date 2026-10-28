"""Key stores (stub)."""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from antifaz.keys.pepper import PepperRing
from antifaz.keys.records import (
    DEFAULT_LIFETIME,
    CreatedKey,
    Expiry,
    KeyRecord,
    Scope,
    VerifyResult,
)

type Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


@runtime_checkable
class KeyStore(Protocol):
    def create(
        self,
        name: str,
        scopes: Iterable[Scope],
        *,
        expires_in: timedelta | Expiry = DEFAULT_LIFETIME,
        per_minute_limit: int | None = None,
        per_day_limit: int | None = None,
    ) -> CreatedKey: ...

    def get(self, key_id: int) -> KeyRecord | None: ...

    def list(self) -> tuple[KeyRecord, ...]: ...

    def revoke(self, key_id: int) -> KeyRecord | None: ...

    def verify(self, presented: object) -> VerifyResult: ...


class MemoryKeyStore:
    def __init__(
        self, peppers: PepperRing, clock: Clock = utc_now, records: Iterable[KeyRecord] = ()
    ) -> None:
        raise NotImplementedError

    def create(
        self,
        name: str,
        scopes: Iterable[Scope],
        *,
        expires_in: timedelta | Expiry = DEFAULT_LIFETIME,
        per_minute_limit: int | None = None,
        per_day_limit: int | None = None,
    ) -> CreatedKey:
        raise NotImplementedError

    def get(self, key_id: int) -> KeyRecord | None:
        raise NotImplementedError

    def list(self) -> tuple[KeyRecord, ...]:
        raise NotImplementedError

    def revoke(self, key_id: int) -> KeyRecord | None:
        raise NotImplementedError

    def verify(self, presented: object) -> VerifyResult:
        raise NotImplementedError
