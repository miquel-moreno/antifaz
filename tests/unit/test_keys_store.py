"""Key records and the in-memory store (issue 121, PR 8a, ADR-0020)."""

import copy
import pickle
from datetime import UTC, datetime, timedelta, timezone

import pytest

from antifaz.keys.format import KEY_LENGTH, PREFIX, parse_key
from antifaz.keys.pepper import PepperRing
from antifaz.keys.records import (
    DEFAULT_LIFETIME,
    HASH_BYTES,
    MAX_NAME_LENGTH,
    Caller,
    CreatedKey,
    Expiry,
    KeyRecord,
    RefusalReason,
    Refused,
    Scope,
    Verified,
)
from antifaz.keys.store import KeyStore, MemoryKeyStore

PEPPER_1 = b"test-pepper-" + b"1" * 32
PEPPER_2 = b"test-pepper-" + b"2" * 32
START = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
ALL_SCOPES = frozenset(Scope)


class FakeClock:
    """A UTC clock the test moves by hand."""

    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def make_store(
    clock: FakeClock | None = None, ring: PepperRing | None = None
) -> tuple[MemoryKeyStore, FakeClock]:
    clock = clock or FakeClock()
    return MemoryKeyStore(ring or PepperRing({1: PEPPER_1}, current=1), clock), clock


def refused(reason: RefusalReason) -> Refused:
    return Refused(reason)


# --- scopes ------------------------------------------------------------------------------


def test_the_scopes_are_the_four_route_groups() -> None:
    assert {scope.value for scope in Scope} == {
        "openai_chat",  # POST /v1/chat/completions
        "anthropic_messages",  # POST /v1/messages and /v1/messages/count_tokens
        "models",  # GET /v1/models
        "scan",  # POST /antifaz/scan
    }


def test_the_refusal_reasons_are_a_closed_list() -> None:
    assert {reason.value for reason in RefusalReason} == {
        "malformed",
        "unknown",
        "revoked",
        "expired",
    }


# --- create, get, list -------------------------------------------------------------------


def test_create_returns_the_clear_key_once_and_a_record() -> None:
    store, _ = make_store()

    created = store.create("billing-bot", {Scope.OPENAI_CHAT})

    assert isinstance(created, CreatedKey)
    assert len(created.key) == KEY_LENGTH
    assert created.key.startswith(PREFIX)
    assert parse_key(created.key) == created.key
    record = created.record
    assert record.id == 1
    assert record.name == "billing-bot"
    assert record.scopes == frozenset({Scope.OPENAI_CHAT})
    assert record.created_at == START
    assert record.expires_at == START + timedelta(days=90)
    assert record.revoked_at is None
    assert record.pepper_version == 1
    assert len(record.key_hash) == HASH_BYTES
    assert record.per_minute_limit is None
    assert record.per_day_limit is None


def test_the_default_lifetime_is_90_days() -> None:
    assert timedelta(days=90) == DEFAULT_LIFETIME


def test_ids_are_fixed_numbers_that_go_up_and_are_never_reused() -> None:
    store, _ = make_store()

    ids = [store.create(f"app-{n}", ALL_SCOPES).record.id for n in range(3)]
    store.revoke(2)

    assert ids == [1, 2, 3]
    assert store.create("app-4", ALL_SCOPES).record.id == 4


def test_get_and_list_return_the_records_in_id_order() -> None:
    store, _ = make_store()
    first = store.create("first", {Scope.SCAN}).record
    second = store.create("second", {Scope.MODELS}).record

    assert store.get(1) == first
    assert store.get(2) == second
    assert store.get(3) is None
    assert store.get(0) is None
    assert store.list() == (first, second)


def test_an_empty_store_lists_nothing() -> None:
    store, _ = make_store()

    assert store.list() == ()
    assert store.verify("anything") == refused(RefusalReason.MALFORMED)


def test_create_with_a_lifetime_and_limits() -> None:
    store, _ = make_store()

    record = store.create(
        "nightly",
        [Scope.ANTHROPIC_MESSAGES, Scope.ANTHROPIC_MESSAGES],
        expires_in=timedelta(hours=1),
        per_minute_limit=60,
        per_day_limit=1000,
    ).record

    assert record.scopes == frozenset({Scope.ANTHROPIC_MESSAGES})
    assert record.expires_at == START + timedelta(hours=1)
    assert record.per_minute_limit == 60
    assert record.per_day_limit == 1000


def test_no_expiry_only_when_asked_explicitly() -> None:
    store, clock = make_store()

    created = store.create("forever", ALL_SCOPES, expires_in=Expiry.NEVER)
    clock.advance(timedelta(days=36500))

    assert created.record.expires_at is None
    assert isinstance(store.verify(created.key), Verified)


@pytest.mark.parametrize("expires_in", [None, timedelta(0), timedelta(seconds=-1), 90, "never"])
def test_a_bad_lifetime_is_refused(expires_in: object) -> None:
    store, _ = make_store()

    with pytest.raises((ValueError, TypeError)):
        store.create("app", ALL_SCOPES, expires_in=expires_in)  # type: ignore[arg-type]
    assert store.list() == ()


@pytest.mark.parametrize(
    "name",
    ["", " app", "app ", "a" * (MAX_NAME_LENGTH + 1), "line\nbreak", "tab\there", "\x00", 7],
)
def test_a_bad_name_is_refused(name: object) -> None:
    store, _ = make_store()

    with pytest.raises((ValueError, TypeError)):
        store.create(name, ALL_SCOPES)  # type: ignore[arg-type]
    assert store.list() == ()


def test_a_name_of_the_maximum_length_and_with_inner_spaces_is_accepted() -> None:
    store, _ = make_store()

    assert store.create("a" * MAX_NAME_LENGTH, ALL_SCOPES).record.name == "a" * MAX_NAME_LENGTH
    assert store.create("Billing bot · ES", ALL_SCOPES).record.name == "Billing bot · ES"


@pytest.mark.parametrize("scopes", [[], ["openai_chat"], [Scope.SCAN, "scan"], "scan", None])
def test_bad_scopes_are_refused(scopes: object) -> None:
    store, _ = make_store()

    with pytest.raises((ValueError, TypeError)):
        store.create("app", scopes)  # type: ignore[arg-type]
    assert store.list() == ()


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "10"])
@pytest.mark.parametrize("which", ["per_minute_limit", "per_day_limit"])
def test_bad_limits_are_refused(which: str, limit: object) -> None:
    store, _ = make_store()

    with pytest.raises((ValueError, TypeError)):
        store.create("app", ALL_SCOPES, **{which: limit})  # type: ignore[arg-type]
    assert store.list() == ()


# --- verify --------------------------------------------------------------------------------


def test_a_good_key_verifies_to_a_caller_with_its_id_and_scopes() -> None:
    store, _ = make_store()
    store.create("other", ALL_SCOPES)
    created = store.create("app", {Scope.OPENAI_CHAT, Scope.MODELS})

    result = store.verify(created.key)

    assert result == Verified(Caller(key_id=2, scopes=frozenset({Scope.OPENAI_CHAT, Scope.MODELS})))


def test_the_caller_holds_only_the_id_and_the_scopes() -> None:
    assert Caller.__slots__ == ("key_id", "scopes")
    assert Verified.__slots__ == ("caller",)
    assert Refused.__slots__ == ("reason",)


def test_a_malformed_key_is_refused_before_any_lookup() -> None:
    store, _ = make_store()
    key = store.create("app", ALL_SCOPES).key

    assert store.verify(key[:-1]) == refused(RefusalReason.MALFORMED)
    assert store.verify(" " + key) == refused(RefusalReason.MALFORMED)
    assert store.verify(key.encode()) == refused(RefusalReason.MALFORMED)
    assert store.verify(None) == refused(RefusalReason.MALFORMED)


def test_a_well_formed_key_that_was_never_created_is_unknown() -> None:
    store, _ = make_store()
    store.create("app", ALL_SCOPES)
    stranger, _ = make_store()
    other_key = stranger.create("elsewhere", ALL_SCOPES).key

    assert store.verify(other_key) == refused(RefusalReason.UNKNOWN)


def test_the_same_key_with_another_pepper_is_unknown() -> None:
    store, _ = make_store()
    key = store.create("app", ALL_SCOPES).key
    other = MemoryKeyStore(PepperRing({1: PEPPER_2}, current=1), FakeClock(), store.list())

    assert other.verify(key) == refused(RefusalReason.UNKNOWN)


def test_expiry_boundary_the_key_works_until_just_before_expires_at() -> None:
    store, clock = make_store()
    key = store.create("app", ALL_SCOPES, expires_in=timedelta(minutes=10)).key

    clock.now = START + timedelta(minutes=10) - timedelta(microseconds=1)
    assert isinstance(store.verify(key), Verified)

    clock.now = START + timedelta(minutes=10)  # exactly expires_at: expired
    assert store.verify(key) == refused(RefusalReason.EXPIRED)

    clock.advance(timedelta(days=1))
    assert store.verify(key) == refused(RefusalReason.EXPIRED)


def test_revoke_stops_the_key_at_once_and_keeps_the_record() -> None:
    store, clock = make_store()
    created = store.create("app", ALL_SCOPES)
    clock.advance(timedelta(minutes=5))

    revoked = store.revoke(created.record.id)

    assert revoked is not None
    assert revoked.revoked_at == START + timedelta(minutes=5)
    assert store.verify(created.key) == refused(RefusalReason.REVOKED)
    assert store.get(created.record.id) == revoked
    assert store.list() == (revoked,)


def test_revoking_twice_keeps_the_first_time() -> None:
    store, clock = make_store()
    store.create("app", ALL_SCOPES)
    first = store.revoke(1)
    clock.advance(timedelta(hours=1))

    second = store.revoke(1)

    assert first is not None
    assert second is not None
    assert second.revoked_at == first.revoked_at == START


def test_revoking_an_unknown_id_changes_nothing() -> None:
    store, _ = make_store()
    created = store.create("app", ALL_SCOPES)

    assert store.revoke(99) is None
    assert isinstance(store.verify(created.key), Verified)


def test_a_revoked_and_expired_key_is_reported_as_revoked() -> None:
    store, clock = make_store()
    key = store.create("app", ALL_SCOPES, expires_in=timedelta(minutes=1)).key
    store.revoke(1)
    clock.advance(timedelta(hours=1))

    assert store.verify(key) == refused(RefusalReason.REVOKED)


# --- pepper rotation -----------------------------------------------------------------------


def test_after_a_rotation_old_keys_still_verify_and_new_ones_use_the_new_version() -> None:
    old_store, clock = make_store()
    old_key = old_store.create("old", ALL_SCOPES).key
    rotated = PepperRing({1: PEPPER_1, 2: PEPPER_2}, current=2)

    store = MemoryKeyStore(rotated, clock, old_store.list())
    new = store.create("new", ALL_SCOPES)

    assert new.record.id == 2
    assert new.record.pepper_version == 2
    assert store.verify(old_key) == Verified(Caller(1, ALL_SCOPES))
    assert store.verify(new.key) == Verified(Caller(2, ALL_SCOPES))


def test_dropping_a_pepper_version_makes_its_keys_unknown() -> None:
    old_store, clock = make_store()
    old_key = old_store.create("old", ALL_SCOPES).key

    store = MemoryKeyStore(PepperRing({2: PEPPER_2}, current=2), clock, old_store.list())

    assert store.verify(old_key) == refused(RefusalReason.UNKNOWN)


def test_loaded_records_with_a_repeated_id_or_hash_are_refused() -> None:
    old_store, clock = make_store()
    record = old_store.create("old", ALL_SCOPES).record
    ring = PepperRing({1: PEPPER_1}, current=1)

    with pytest.raises(ValueError):
        MemoryKeyStore(ring, clock, [record, record])


def test_loaded_records_must_be_records() -> None:
    with pytest.raises(TypeError):
        MemoryKeyStore(PepperRing({1: PEPPER_1}, current=1), FakeClock(), ["afz_"])  # type: ignore[list-item]


# --- the clock -----------------------------------------------------------------------------


def test_a_clock_in_another_utc_zone_is_normalised_to_utc() -> None:
    store, _ = make_store(
        FakeClock(datetime(2026, 10, 28, 12, 0, tzinfo=timezone(timedelta(0), "Z")))
    )

    record = store.create("app", ALL_SCOPES).record

    assert record.created_at.tzinfo is UTC
    assert record.created_at == START


def test_a_clock_in_local_time_is_converted_to_utc() -> None:
    madrid = datetime(2026, 10, 28, 13, 0, tzinfo=timezone(timedelta(hours=1)))  # CET
    store, _ = make_store(FakeClock(madrid))

    assert store.create("app", ALL_SCOPES).record.created_at == START


def test_a_naive_clock_is_refused() -> None:
    store, _ = make_store(FakeClock(datetime(2026, 10, 28, 12, 0)))

    with pytest.raises(ValueError, match="UTC"):
        store.create("app", ALL_SCOPES)


def test_the_default_clock_is_utc() -> None:
    store = MemoryKeyStore(PepperRing({1: PEPPER_1}, current=1))

    record = store.create("app", ALL_SCOPES).record

    assert record.created_at.tzinfo is UTC
    assert abs(datetime.now(UTC) - record.created_at) < timedelta(minutes=1)


# --- the record is closed ------------------------------------------------------------------


def _record(**changes: object) -> KeyRecord:
    fields: dict[str, object] = {
        "id": 1,
        "name": "app",
        "scopes": ALL_SCOPES,
        "created_at": START,
        "expires_at": START + DEFAULT_LIFETIME,
        "revoked_at": None,
        "key_hash": b"h" * HASH_BYTES,
        "pepper_version": 1,
        "per_minute_limit": None,
        "per_day_limit": None,
    }
    fields.update(changes)
    return KeyRecord(**fields)  # type: ignore[arg-type]


def test_a_valid_record_is_accepted() -> None:
    assert _record().id == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"id": 0},
        {"id": True},
        {"id": "1"},
        {"name": ""},
        {"scopes": frozenset()},
        {"scopes": {Scope.SCAN}},
        {"scopes": frozenset({"scan"})},
        {"created_at": datetime(2026, 1, 1)},
        {"created_at": datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1)))},
        {"expires_at": START},
        {"expires_at": START - timedelta(seconds=1)},
        {"expires_at": datetime(2027, 1, 1)},
        {"revoked_at": START - timedelta(seconds=1)},
        {"revoked_at": datetime(2027, 1, 1)},
        {"key_hash": b"short"},
        {"key_hash": "h" * HASH_BYTES},
        {"pepper_version": 0},
        {"pepper_version": True},
        {"per_minute_limit": 0},
        {"per_day_limit": -5},
    ],
)
def test_a_record_with_a_bad_field_is_refused(changes: dict[str, object]) -> None:
    with pytest.raises((ValueError, TypeError)):
        _record(**changes)


def test_records_are_frozen() -> None:
    record = _record()

    with pytest.raises(AttributeError):
        record.revoked_at = START  # type: ignore[misc]


def test_record_equality_never_compares_the_hash() -> None:
    assert _record(key_hash=b"a" * HASH_BYTES) == _record(key_hash=b"b" * HASH_BYTES)


# --- the store holds no secret that can travel ---------------------------------------------


def test_the_store_cannot_be_pickled_or_copied() -> None:
    store, _ = make_store()
    store.create("app", ALL_SCOPES)

    for action in (pickle.dumps, copy.copy, copy.deepcopy):
        with pytest.raises(TypeError):
            action(store)


def test_the_created_key_cannot_be_pickled_or_copied() -> None:
    store, _ = make_store()
    created = store.create("app", ALL_SCOPES)

    for action in (pickle.dumps, copy.copy, copy.deepcopy):
        with pytest.raises(TypeError):
            action(created)


def test_the_memory_store_follows_the_protocol() -> None:
    store, _ = make_store()

    assert isinstance(store, KeyStore)


def test_the_store_needs_a_pepper_ring() -> None:
    with pytest.raises(TypeError):
        MemoryKeyStore({1: PEPPER_1})  # type: ignore[arg-type]


def test_a_clock_that_returns_no_datetime_is_refused() -> None:
    ring = PepperRing({1: PEPPER_1}, current=1)
    store = MemoryKeyStore(ring, lambda: "now")  # type: ignore[arg-type,return-value]

    with pytest.raises(ValueError, match="UTC"):
        store.create("app", ALL_SCOPES)
    assert store.list() == ()


def test_the_store_repr_shows_only_how_many_keys_it_has() -> None:
    store, _ = make_store()
    store.create("app", ALL_SCOPES)

    assert repr(store) == str(store) == "MemoryKeyStore(keys=1)"
