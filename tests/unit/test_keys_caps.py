"""Common caps of the key stores (issue 121, PR 8b-1, decision D14).

PostgreSQL keeps the limits in `integer` columns and the pepper version in a `smallint`: the
memory store must refuse exactly what PostgreSQL cannot hold, so both stores refuse the same.
"""

import secrets
from datetime import UTC, datetime, timedelta

import pytest

from antifaz.keys import KeyRecord, MemoryKeyStore, PepperRing, Scope
from antifaz.keys.records import MAX_LIMIT, MAX_PEPPER_VERSION

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def pepper() -> bytes:
    return secrets.token_bytes(32)


def record(**changes: object) -> KeyRecord:
    values: dict[str, object] = {
        "id": 1,
        "name": "facturas",
        "scopes": frozenset({Scope.SCAN}),
        "created_at": NOW,
        "expires_at": NOW + timedelta(days=90),
        "revoked_at": None,
        "key_hash": bytes(32),
        "pepper_version": 1,
        "per_minute_limit": None,
        "per_day_limit": None,
    }
    values |= changes
    return KeyRecord(**values)  # type: ignore[arg-type]


def test_the_caps_are_the_postgresql_column_ranges() -> None:
    assert MAX_LIMIT == 2**31 - 1  # integer
    assert MAX_PEPPER_VERSION == 2**15 - 1  # smallint


@pytest.mark.parametrize("field", ["per_minute_limit", "per_day_limit"])
def test_a_record_takes_a_limit_up_to_the_cap(field: str) -> None:
    assert getattr(record(**{field: MAX_LIMIT}), field) == MAX_LIMIT


@pytest.mark.parametrize("field", ["per_minute_limit", "per_day_limit"])
def test_a_record_refuses_a_limit_over_the_cap(field: str) -> None:
    with pytest.raises(ValueError, match="limit"):
        record(**{field: MAX_LIMIT + 1})


def test_a_record_takes_a_pepper_version_up_to_the_cap() -> None:
    assert record(pepper_version=MAX_PEPPER_VERSION).pepper_version == MAX_PEPPER_VERSION


def test_a_record_refuses_a_pepper_version_over_the_cap() -> None:
    with pytest.raises(ValueError, match="pepper version"):
        record(pepper_version=MAX_PEPPER_VERSION + 1)


def test_the_pepper_ring_takes_a_version_up_to_the_cap() -> None:
    ring = PepperRing({MAX_PEPPER_VERSION: pepper()}, current=MAX_PEPPER_VERSION)

    assert ring.versions == (MAX_PEPPER_VERSION,)


def test_the_pepper_ring_refuses_a_version_over_the_cap() -> None:
    with pytest.raises(ValueError, match="pepper version"):
        PepperRing({MAX_PEPPER_VERSION + 1: pepper()}, current=MAX_PEPPER_VERSION + 1)


@pytest.mark.parametrize("field", ["per_minute_limit", "per_day_limit"])
async def test_the_memory_store_refuses_a_limit_over_the_cap(field: str) -> None:
    store = MemoryKeyStore(PepperRing({1: pepper()}, current=1), lambda: NOW)

    with pytest.raises(ValueError, match="limit"):
        await store.create("facturas", {Scope.SCAN}, **{field: MAX_LIMIT + 1})  # type: ignore[arg-type]

    assert await store.list() == ()
