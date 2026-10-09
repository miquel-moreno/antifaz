"""Panel sessions with a real logout (issue 53, ADR-0018): web/sessions.py.

A fake clock drives every expiry: no test sleeps.
"""

import dataclasses
import hashlib

import pytest

from antifaz.web.sessions import (
    IDLE_SECONDS,
    MAX_AGE_SECONDS,
    CloseReason,
    MemorySessionStore,
    Session,
    SessionStore,
    fingerprint,
)


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Subscriber:
    def __init__(self) -> None:
        self.closed: list[tuple[str, CloseReason]] = []

    def __call__(self, key: str, reason: CloseReason) -> None:
        self.closed.append((key, reason))


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock: Clock) -> MemorySessionStore:
    return MemorySessionStore(clock=clock)


def test_the_memory_store_is_a_session_store(store: MemorySessionStore) -> None:
    def takes(s: SessionStore) -> SessionStore:
        return s

    assert takes(store) is store


def test_each_create_gives_a_new_256_bit_id_and_csrf_token(store: MemorySessionStore) -> None:
    made = [store.create() for _ in range(20)]
    ids = {session_id for session_id, _ in made}
    tokens = {session.csrf_token for _, session in made}

    assert len(ids) == 20 and len(tokens) == 20
    assert all(len(session_id) >= 43 for session_id in ids)  # 32 bytes in URL-safe base64
    assert ids.isdisjoint(tokens)


def test_only_the_fingerprint_of_the_id_is_stored(store: MemorySessionStore) -> None:
    session_id, session = store.create()

    assert session.fingerprint == hashlib.sha256(session_id.encode()).hexdigest()
    assert session.fingerprint == fingerprint(session_id)
    assert session_id not in [str(v) for v in dataclasses.asdict(session).values()]
    assert session_id not in repr(session)
    assert session_id not in repr(vars(store))
    assert [f.name for f in dataclasses.fields(Session)] == [
        "fingerprint",
        "created",
        "last_used",
        "csrf_token",
    ]


def test_get_finds_the_session_and_refreshes_its_last_use(
    store: MemorySessionStore, clock: Clock
) -> None:
    session_id, session = store.create()
    clock.advance(600)

    found = store.get(session_id)

    assert found is session
    assert found.created == 1000.0
    assert found.last_used == 1600.0


def test_an_unknown_or_forged_id_finds_nothing(store: MemorySessionStore) -> None:
    session_id, session = store.create()

    assert store.get("") is None
    assert store.get(session_id + "x") is None
    assert store.get(session.fingerprint) is None  # the fingerprint is not a cookie


def test_30_minutes_without_use_expire_the_session(store: MemorySessionStore, clock: Clock) -> None:
    subscriber = Subscriber()
    store.on_close(subscriber)
    session_id, session = store.create()
    clock.advance(IDLE_SECONDS - 1)
    assert store.get(session_id) is session
    clock.advance(IDLE_SECONDS - 1)
    assert store.get(session_id) is session  # each use moves the idle limit
    clock.advance(IDLE_SECONDS)

    assert store.get(session_id) is None
    assert subscriber.closed == [(session.fingerprint, "expired")]
    assert len(store) == 0


def test_8_hours_is_the_limit_even_when_used(store: MemorySessionStore, clock: Clock) -> None:
    subscriber = Subscriber()
    store.on_close(subscriber)
    session_id, session = store.create()
    while clock.now - session.created < MAX_AGE_SECONDS - 600:
        clock.advance(600)
        assert store.get(session_id) is session
    clock.advance(600)

    assert store.get(session_id) is None
    assert subscriber.closed == [(session.fingerprint, "expired")]


def test_is_active_checks_without_counting_a_use(store: MemorySessionStore, clock: Clock) -> None:
    session_id, session = store.create()
    clock.advance(IDLE_SECONDS - 1)

    assert store.is_active(session.fingerprint)
    assert session.last_used == 1000.0
    clock.advance(1)
    assert not store.is_active(session.fingerprint)
    assert store.get(session_id) is None
    assert not store.is_active("0" * 64)


def test_delete_closes_now_and_tells_the_subscribers_why(store: MemorySessionStore) -> None:
    subscriber = Subscriber()
    store.on_close(subscriber)
    session_id, session = store.create()

    assert store.delete(session_id)
    assert store.get(session_id) is None
    assert not store.is_active(session.fingerprint)
    assert not store.delete(session_id)  # once only
    assert subscriber.closed == [(session.fingerprint, "logout")]


def test_every_subscriber_hears_every_reason(store: MemorySessionStore, clock: Clock) -> None:
    first, second = Subscriber(), Subscriber()
    store.on_close(first)
    store.on_close(second)
    logged_out, _ = store.create()
    expired, expired_session = store.create()
    store.delete(logged_out, "logout")
    clock.advance(IDLE_SECONDS)
    store.get(expired)
    small = MemorySessionStore(clock=clock, max_sessions=1)
    small.on_close(first)
    _, evicted = small.create()
    small.create()

    reasons = [reason for _, reason in first.closed]
    assert reasons == ["logout", "expired", "evicted"]
    assert first.closed[1] == (expired_session.fingerprint, "expired")
    assert first.closed[2] == (evicted.fingerprint, "evicted")
    assert [reason for _, reason in second.closed] == ["logout", "expired"]


def test_unsubscribe_stops_the_calls_and_can_be_called_twice(store: MemorySessionStore) -> None:
    subscriber = Subscriber()
    unsubscribe = store.on_close(subscriber)
    unsubscribe()
    unsubscribe()
    session_id, _ = store.create()
    store.delete(session_id)

    assert subscriber.closed == []


def test_a_failing_subscriber_does_not_keep_the_others_from_hearing(
    store: MemorySessionStore,
) -> None:
    def broken(key: str, reason: CloseReason) -> None:
        raise RuntimeError("subscriber failed")

    subscriber = Subscriber()
    store.on_close(broken)
    store.on_close(subscriber)
    session_id, session = store.create()

    with pytest.raises(RuntimeError):
        store.delete(session_id)
    assert subscriber.closed == [(session.fingerprint, "logout")]
    assert store.get(session_id) is None  # closed anyway


def test_a_full_store_evicts_the_oldest_and_always_creates(clock: Clock) -> None:
    store = MemorySessionStore(clock=clock, max_sessions=3)
    subscriber = Subscriber()
    store.on_close(subscriber)
    made = []
    for _ in range(3):
        made.append(store.create())
        clock.advance(1)
    store.get(made[0][0])  # used recently, but still the oldest by creation

    new_id, new = store.create()

    assert len(store) == 3
    assert store.get(new_id) is new
    assert store.get(made[0][0]) is None
    assert store.get(made[1][0]) is made[1][1]
    assert subscriber.closed == [(made[0][1].fingerprint, "evicted")]


def test_a_full_store_never_refuses_a_login(clock: Clock) -> None:
    store = MemorySessionStore(clock=clock, max_sessions=32)
    for _ in range(200):
        session_id, session = store.create()
        assert store.get(session_id) is session
        assert len(store) <= 32


def test_expired_sessions_go_before_any_live_one_is_evicted(clock: Clock) -> None:
    store = MemorySessionStore(clock=clock, max_sessions=2)
    subscriber = Subscriber()
    store.on_close(subscriber)
    old_id, old = store.create()
    clock.advance(IDLE_SECONDS - 10)
    live_id, live = store.create()
    clock.advance(10)  # the first one is idle now; the second is not

    store.create()

    assert subscriber.closed == [(old.fingerprint, "expired")]
    assert store.get(live_id) is live
    assert store.get(old_id) is None


def test_the_store_needs_room_for_one_session() -> None:
    with pytest.raises(ValueError):
        MemorySessionStore(max_sessions=0)
