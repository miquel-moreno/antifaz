"""Panel sessions with a real logout (issue 53, ADR-0018): web/sessions.py.

A fake clock drives every expiry: no test sleeps. Since 11b-2b: every session is bound to the
scheme it was made over (a lookup over the other one closes it), every close states its reason,
watch() tells one session's subscriber, and the CSRF token owes nothing to the session id.
"""

import asyncio
import base64
import dataclasses
import hashlib
import hmac
import inspect
import secrets
import typing

import pytest

from antifaz.web import sessions
from antifaz.web.sessions import (
    IDLE_SECONDS,
    MAX_AGE_SECONDS,
    SCHEMES,
    CloseReason,
    MemorySessionStore,
    Session,
    SessionScheme,
    SessionStore,
    fingerprint,
    watch,
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


def same(found: Session | None, session: Session) -> bool:
    """The store hands out a new (frozen) Session on each use: compare by fingerprint."""
    return found is not None and found.fingerprint == session.fingerprint


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
    made = [store.create("https") for _ in range(20)]
    ids = {session_id for session_id, _ in made}
    tokens = {session.csrf_token for _, session in made}

    assert len(ids) == 20 and len(tokens) == 20
    assert all(len(session_id) >= 43 for session_id in ids)  # 32 bytes in URL-safe base64
    assert ids.isdisjoint(tokens)


def test_only_the_fingerprint_of_the_id_is_stored(store: MemorySessionStore) -> None:
    session_id, session = store.create("https")

    assert session.fingerprint == hashlib.sha256(session_id.encode()).hexdigest()
    assert session.fingerprint == fingerprint(session_id)
    assert session_id not in [str(v) for v in dataclasses.asdict(session).values()]
    assert session_id not in repr(session)
    assert session_id not in repr(vars(store))
    assert [f.name for f in dataclasses.fields(Session)] == [
        "fingerprint",
        "scheme",
        "created",
        "last_used",
        "csrf_token",
    ]


def test_get_finds_the_session_and_refreshes_its_last_use(
    store: MemorySessionStore, clock: Clock
) -> None:
    session_id, session = store.create("https")
    clock.advance(600)

    found = store.get(session_id, "https")

    assert same(found, session)
    assert found is not None
    assert found.created == 1000.0
    assert found.last_used == 1600.0
    assert session.last_used == 1000.0  # frozen: the old object is never changed
    with pytest.raises(dataclasses.FrozenInstanceError):
        found.last_used = 0.0  # type: ignore[misc]


def test_the_repr_of_a_session_shows_no_secret(store: MemorySessionStore) -> None:
    session_id, session = store.create("https")
    text = repr(session) + str(session)

    for secret in (session_id, session.fingerprint, session.csrf_token):
        assert secret not in text
    assert "created" in text


def test_an_unknown_or_forged_id_finds_nothing(store: MemorySessionStore) -> None:
    session_id, session = store.create("https")

    assert store.get("", "https") is None
    assert store.get(session_id + "x", "https") is None
    assert store.get(session.fingerprint, "https") is None  # the fingerprint is not a cookie


def test_30_minutes_without_use_expire_the_session(store: MemorySessionStore, clock: Clock) -> None:
    subscriber = Subscriber()
    store.on_close(subscriber)
    session_id, session = store.create("https")
    clock.advance(IDLE_SECONDS - 1)
    assert same(store.get(session_id, "https"), session)
    clock.advance(IDLE_SECONDS - 1)
    assert same(store.get(session_id, "https"), session)  # each use moves the idle limit
    clock.advance(IDLE_SECONDS)

    assert store.get(session_id, "https") is None
    assert subscriber.closed == [(session.fingerprint, "expired")]
    assert len(store) == 0


def test_8_hours_is_the_limit_even_when_used(store: MemorySessionStore, clock: Clock) -> None:
    subscriber = Subscriber()
    store.on_close(subscriber)
    session_id, session = store.create("https")
    while clock.now - session.created < MAX_AGE_SECONDS - 600:
        clock.advance(600)
        assert same(store.get(session_id, "https"), session)
    clock.advance(600)

    assert store.get(session_id, "https") is None
    assert subscriber.closed == [(session.fingerprint, "expired")]


def test_is_active_checks_without_counting_a_use(store: MemorySessionStore, clock: Clock) -> None:
    session_id, session = store.create("https")
    clock.advance(IDLE_SECONDS - 1)

    assert store.is_active(session.fingerprint)
    assert session.last_used == 1000.0
    clock.advance(1)
    assert not store.is_active(session.fingerprint)
    assert store.get(session_id, "https") is None
    assert not store.is_active("0" * 64)


def test_delete_closes_now_and_tells_the_subscribers_why(store: MemorySessionStore) -> None:
    subscriber = Subscriber()
    store.on_close(subscriber)
    session_id, session = store.create("https")

    assert store.delete(session_id, "logout")
    assert store.get(session_id, "https") is None
    assert not store.is_active(session.fingerprint)
    assert not store.delete(session_id, "logout")  # once only
    assert subscriber.closed == [(session.fingerprint, "logout")]


def test_every_subscriber_hears_every_reason(store: MemorySessionStore, clock: Clock) -> None:
    first, second = Subscriber(), Subscriber()
    store.on_close(first)
    store.on_close(second)
    logged_out, _ = store.create("https")
    expired, expired_session = store.create("https")
    store.delete(logged_out, "logout")
    clock.advance(IDLE_SECONDS)
    store.get(expired, "https")
    small = MemorySessionStore(clock=clock, max_sessions=1)
    small.on_close(first)
    _, evicted = small.create("https")
    small.create("https")

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
    session_id, _ = store.create("https")
    store.delete(session_id, "logout")

    assert subscriber.closed == []


def broken(key: str, reason: CloseReason) -> None:
    raise RuntimeError(f"subscriber failed for {key}")


def test_a_failing_subscriber_never_breaks_a_close_and_the_others_still_hear(
    store: MemorySessionStore, caplog: pytest.LogCaptureFixture
) -> None:
    subscriber = Subscriber()
    store.on_close(broken)
    store.on_close(subscriber)
    session_id, session = store.create("https")

    assert store.delete(session_id, "logout")  # no exception
    assert subscriber.closed == [(session.fingerprint, "logout")]
    assert store.get(session_id, "https") is None
    assert "session close subscriber failed: RuntimeError" in caplog.text
    assert session.fingerprint not in caplog.text
    assert session.fingerprint not in "".join(str(r.__dict__) for r in caplog.records)


def test_a_failing_subscriber_never_blocks_a_login_when_the_store_is_full(clock: Clock) -> None:
    store = MemorySessionStore(clock=clock, max_sessions=1)
    store.on_close(broken)
    store.create("https")

    session_id, session = store.create("https")  # evicts the first one: the subscriber fails

    assert len(store) == 1
    assert same(store.get(session_id, "https"), session)


def test_a_failing_subscriber_never_breaks_get_or_is_active_on_expiry(
    store: MemorySessionStore, clock: Clock
) -> None:
    store.on_close(broken)
    first_id, _ = store.create("https")
    _, second = store.create("https")
    clock.advance(IDLE_SECONDS)

    assert store.get(first_id, "https") is None
    assert not store.is_active(second.fingerprint)
    store.create("https")  # _drop_expired with a failing subscriber
    assert len(store) == 1


def test_a_full_store_evicts_the_oldest_and_always_creates(clock: Clock) -> None:
    store = MemorySessionStore(clock=clock, max_sessions=3)
    subscriber = Subscriber()
    store.on_close(subscriber)
    made = []
    for _ in range(3):
        made.append(store.create("https"))
        clock.advance(1)
    store.get(made[0][0], "https")  # used recently, but still the oldest by creation

    new_id, new = store.create("https")

    assert len(store) == 3
    assert same(store.get(new_id, "https"), new)
    assert store.get(made[0][0], "https") is None
    assert same(store.get(made[1][0], "https"), made[1][1])
    assert subscriber.closed == [(made[0][1].fingerprint, "evicted")]


def test_a_full_store_never_refuses_a_login(clock: Clock) -> None:
    store = MemorySessionStore(clock=clock, max_sessions=32)
    for _ in range(200):
        session_id, session = store.create("https")
        assert same(store.get(session_id, "https"), session)
        assert len(store) <= 32


def test_expired_sessions_go_before_any_live_one_is_evicted(clock: Clock) -> None:
    store = MemorySessionStore(clock=clock, max_sessions=2)
    subscriber = Subscriber()
    store.on_close(subscriber)
    old_id, old = store.create("https")
    clock.advance(IDLE_SECONDS - 10)
    live_id, live = store.create("https")
    clock.advance(10)  # the first one is idle now; the second is not

    store.create("https")

    assert subscriber.closed == [(old.fingerprint, "expired")]
    assert same(store.get(live_id, "https"), live)
    assert store.get(old_id, "https") is None


def test_the_store_needs_room_for_one_session() -> None:
    with pytest.raises(ValueError):
        MemorySessionStore(max_sessions=0)


# --- Bound to its scheme (11b-2b) -----------------------------------------------------------


def test_the_schemes_and_close_reasons_are_exactly_these() -> None:
    assert {"https", "http-local"} == SCHEMES
    assert set(typing.get_args(SessionScheme.__value__)) == SCHEMES
    assert set(typing.get_args(CloseReason.__value__)) == {
        "logout",
        "replaced",
        "expired",
        "evicted",
        "scheme_mismatch",
        "gone",
    }


@pytest.mark.parametrize("scheme", ["https", "http-local"])
def test_a_session_keeps_its_scheme_and_is_found_over_it(
    store: MemorySessionStore, scheme: SessionScheme
) -> None:
    session_id, session = store.create(scheme)

    assert session.scheme == scheme
    found = store.get(session_id, scheme)
    assert same(found, session)
    assert found is not None and found.scheme == scheme


@pytest.mark.parametrize(("made", "presented"), [("https", "http-local"), ("http-local", "https")])
def test_over_the_other_scheme_the_session_is_refused_and_closed(
    store: MemorySessionStore, made: SessionScheme, presented: SessionScheme
) -> None:
    subscriber = Subscriber()
    store.on_close(subscriber)
    session_id, session = store.create(made)

    assert store.get(session_id, presented) is None
    assert subscriber.closed == [(session.fingerprint, "scheme_mismatch")]
    assert store.get(session_id, made) is None  # gone for its own scheme too
    assert not store.is_active(session.fingerprint)
    assert len(store) == 0


def test_an_expired_session_over_the_other_scheme_is_closed_as_expired(
    store: MemorySessionStore, clock: Clock
) -> None:
    subscriber = Subscriber()
    store.on_close(subscriber)
    session_id, session = store.create("https")
    clock.advance(IDLE_SECONDS)

    assert store.get(session_id, "http-local") is None
    assert subscriber.closed == [(session.fingerprint, "expired")]


@pytest.mark.parametrize("scheme", ["http", "HTTPS", "", "local"])
def test_create_refuses_an_unknown_scheme(store: MemorySessionStore, scheme: str) -> None:
    with pytest.raises(ValueError, match="unknown session scheme"):
        store.create(scheme)  # type: ignore[arg-type]
    assert len(store) == 0


def test_an_unknown_scheme_on_get_never_finds_a_session(store: MemorySessionStore) -> None:
    session_id, _ = store.create("https")

    assert store.get(session_id, "http") is None  # type: ignore[arg-type]
    assert len(store) == 0  # closed: fail closed, never a match


def test_scheme_and_reason_have_no_default() -> None:
    for cls in (MemorySessionStore, SessionStore):
        create = inspect.signature(cls.create).parameters["scheme"]
        get = inspect.signature(cls.get).parameters["scheme"]
        peek = inspect.signature(cls.peek).parameters["scheme"]
        delete = inspect.signature(cls.delete).parameters["reason"]
        for parameter in (create, get, peek, delete):
            assert parameter.default is inspect.Parameter.empty, (cls, parameter)


def test_delete_tells_the_reason_it_was_given(store: MemorySessionStore) -> None:
    subscriber = Subscriber()
    store.on_close(subscriber)
    replaced, first = store.create("https")
    logged_out, second = store.create("http-local")

    assert store.delete(replaced, "replaced")
    assert store.delete(logged_out, "logout")
    assert subscriber.closed == [(first.fingerprint, "replaced"), (second.fingerprint, "logout")]


# --- The CSRF token owes nothing to the session id (Miquel, 11b-2b) -------------------------


def _derivations(value: str) -> set[str]:
    raw = value.encode()
    digests = [
        hashlib.sha256(raw).digest(),
        hashlib.sha512(raw).digest(),
        hashlib.blake2b(raw).digest(),
        hashlib.md5(raw).digest(),  # noqa: S324 - only to prove the token is not this
        hashlib.sha1(raw).digest(),  # noqa: S324 - only to prove the token is not this
        hmac.new(raw, b"csrf", hashlib.sha256).digest(),
    ]
    out = {value, value[::-1], value.upper(), value.lower()}
    for digest in digests:
        out |= {
            digest.hex(),
            base64.b64encode(digest).decode(),
            base64.urlsafe_b64encode(digest).decode(),
            base64.urlsafe_b64encode(digest).decode().rstrip("="),
            base64.urlsafe_b64encode(digest[:32]).decode().rstrip("="),
        }
    return out


def test_the_csrf_token_is_not_the_id_nor_derived_from_it() -> None:
    big = MemorySessionStore(max_sessions=200)
    for scheme in ("https", "http-local"):
        for _ in range(50):
            session_id, session = big.create(scheme)  # type: ignore[arg-type]
            token = session.csrf_token

            assert token != session_id
            assert token != session.fingerprint
            assert token not in _derivations(session_id)
            assert token not in _derivations(session.fingerprint)
            # Not a prefix, suffix or slice of the id (or the other way round).
            for size in (8, 12, 16):
                assert token[:size] not in session_id
                assert token[-size:] not in session_id
                assert session_id[:size] not in token
                assert session_id[-size:] not in token
            assert len(token) >= 43  # 32 random bytes of its own


def test_two_sessions_never_share_a_csrf_token() -> None:
    big = MemorySessionStore(max_sessions=500)
    tokens = [big.create("https")[1].csrf_token for _ in range(250)]
    tokens += [big.create("http-local")[1].csrf_token for _ in range(250)]

    assert len(set(tokens)) == 500


def test_the_csrf_token_is_drawn_apart_from_the_id(
    store: MemorySessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each create() draws twice from secrets: the id and, separately, the token."""
    draws: list[str] = []
    real = secrets.token_urlsafe

    def spy(nbytes: int | None = None) -> str:
        value = real(nbytes)
        draws.append(value)
        return value

    monkeypatch.setattr(sessions.secrets, "token_urlsafe", spy)
    session_id, session = store.create("https")

    assert draws == [session_id, session.csrf_token]


# --- watch(): one session's subscriber, for 11e's SSE ---------------------------------------


class Cut:
    def __init__(self) -> None:
        self.reasons: list[CloseReason] = []

    def __call__(self, reason: CloseReason) -> None:
        self.reasons.append(reason)


def test_watch_tells_only_its_own_session_and_why(store: MemorySessionStore) -> None:
    mine_id, mine = store.create("https")
    other_id, _ = store.create("https")
    cut = Cut()
    watch(store, mine.fingerprint, cut)

    store.delete(other_id, "logout")
    assert cut.reasons == []
    store.delete(mine_id, "logout")
    assert cut.reasons == ["logout"]


@pytest.mark.parametrize("reason", ["logout", "replaced"])
def test_watch_hears_a_delete_with_its_reason(
    store: MemorySessionStore, reason: CloseReason
) -> None:
    session_id, session = store.create("http-local")
    cut = Cut()
    watch(store, session.fingerprint, cut)

    store.delete(session_id, reason)

    assert cut.reasons == [reason]


def test_watch_hears_expired_on_the_next_lookup(store: MemorySessionStore, clock: Clock) -> None:
    session_id, session = store.create("https")
    cut = Cut()
    watch(store, session.fingerprint, cut)
    clock.advance(IDLE_SECONDS)
    assert cut.reasons == []  # no timers: nothing happens until someone looks

    store.get(session_id, "https")

    assert cut.reasons == ["expired"]


def test_watch_hears_evicted_and_scheme_mismatch(clock: Clock) -> None:
    store = MemorySessionStore(clock=clock, max_sessions=1)
    _, first = store.create("https")
    evicted = Cut()
    watch(store, first.fingerprint, evicted)
    second_id, second = store.create("https")
    mismatch = Cut()
    watch(store, second.fingerprint, mismatch)

    store.get(second_id, "http-local")

    assert evicted.reasons == ["evicted"]
    assert mismatch.reasons == ["scheme_mismatch"]


def test_watch_ends_by_itself_after_the_close(store: MemorySessionStore) -> None:
    session_id, session = store.create("https")
    cut = Cut()
    unsubscribe = watch(store, session.fingerprint, cut)
    before = len(store._callbacks)

    store.delete(session_id, "logout")
    store.delete(session_id, "logout")

    assert cut.reasons == ["logout"]
    assert len(store._callbacks) == before - 1
    unsubscribe()  # harmless afterwards


def test_watch_unsubscribe_stops_it(store: MemorySessionStore) -> None:
    session_id, session = store.create("https")
    cut = Cut()
    unsubscribe = watch(store, session.fingerprint, cut)

    unsubscribe()
    store.delete(session_id, "logout")

    assert cut.reasons == []
    assert store._callbacks == []


def test_a_failing_cut_never_breaks_the_close(
    store: MemorySessionStore, caplog: pytest.LogCaptureFixture
) -> None:
    session_id, session = store.create("https")

    def failing(reason: CloseReason) -> None:
        raise RuntimeError(f"cut failed for {session.fingerprint}")

    cut = Cut()
    watch(store, session.fingerprint, failing)
    watch(store, session.fingerprint, cut)

    assert store.delete(session_id, "logout")
    assert cut.reasons == ["logout"]
    assert "session close subscriber failed: RuntimeError" in caplog.text
    assert session.fingerprint not in caplog.text


# --- Review fixes (11b-2b) ------------------------------------------------------------------

# A. watch() on a session that is already closed, unknown or expired.


def test_the_close_reasons_include_gone() -> None:
    assert "gone" in typing.get_args(CloseReason.__value__)


def test_watch_on_an_unknown_key_cuts_at_once_with_gone(store: MemorySessionStore) -> None:
    before = len(store._callbacks)
    cut = Cut()

    unsubscribe = watch(store, "0" * 64, cut)

    assert cut.reasons == ["gone"]
    assert len(store._callbacks) == before
    unsubscribe()  # a no-op, and harmless
    assert cut.reasons == ["gone"]


def test_watch_on_a_session_closed_before_cuts_with_gone(store: MemorySessionStore) -> None:
    session_id, session = store.create("https")
    store.delete(session_id, "logout")
    before = len(store._callbacks)
    cut = Cut()

    watch(store, session.fingerprint, cut)

    assert cut.reasons == ["gone"]
    assert len(store._callbacks) == before


def test_watch_on_a_session_expired_but_not_looked_up_cuts_with_expired(
    store: MemorySessionStore, clock: Clock
) -> None:
    _, session = store.create("https")
    clock.advance(IDLE_SECONDS)
    before = len(store._callbacks)
    cut = Cut()

    watch(store, session.fingerprint, cut)

    assert cut.reasons == ["expired"]  # the check itself closed it, and the watcher heard it
    assert len(store._callbacks) == before
    assert len(store) == 0


def test_a_thousand_watches_on_unknown_keys_leave_no_callback(store: MemorySessionStore) -> None:
    cut = Cut()
    for i in range(1000):
        watch(store, f"{i:064x}", cut)

    assert store._callbacks == []
    assert cut.reasons == ["gone"] * 1000


def test_watch_on_a_live_session_stays_subscribed(store: MemorySessionStore) -> None:
    _, session = store.create("https")
    cut = Cut()

    watch(store, session.fingerprint, cut)

    assert cut.reasons == []
    assert len(store._callbacks) == 1


# B. Deleting an expired session closes it as expired, never with the reason given.


@pytest.mark.parametrize("reason", ["replaced", "logout"])
def test_delete_of_an_expired_session_closes_it_as_expired(
    store: MemorySessionStore, clock: Clock, reason: CloseReason
) -> None:
    subscriber = Subscriber()
    store.on_close(subscriber)
    session_id, session = store.create("https")
    cut = Cut()
    watch(store, session.fingerprint, cut)
    clock.advance(IDLE_SECONDS)

    assert not store.delete(session_id, reason)  # it was no longer a live session
    assert subscriber.closed == [(session.fingerprint, "expired")]
    assert cut.reasons == ["expired"]
    assert len(store) == 0


def test_delete_of_a_live_session_uses_the_reason_given(
    store: MemorySessionStore, clock: Clock
) -> None:
    session_id, session = store.create("https")
    cut = Cut()
    watch(store, session.fingerprint, cut)
    clock.advance(IDLE_SECONDS - 1)

    assert store.delete(session_id, "replaced")
    assert cut.reasons == ["replaced"]


# C. Looking a session up and counting a use are two steps: peek() and touch().


def test_peek_finds_without_counting_a_use(store: MemorySessionStore, clock: Clock) -> None:
    session_id, session = store.create("https")
    clock.advance(600)

    found = store.peek(session_id, "https")

    assert same(found, session)
    assert found is not None and found.last_used == 1000.0
    assert store._sessions[session.fingerprint].last_used == 1000.0


def test_peek_alone_lets_the_session_expire(store: MemorySessionStore, clock: Clock) -> None:
    session_id, _ = store.create("https")
    for _ in range(3):
        clock.advance(IDLE_SECONDS / 2)
        store.peek(session_id, "https")

    assert store.peek(session_id, "https") is None


def test_peek_over_the_other_scheme_closes_the_session(store: MemorySessionStore) -> None:
    subscriber = Subscriber()
    store.on_close(subscriber)
    session_id, session = store.create("https")

    assert store.peek(session_id, "http-local") is None
    assert subscriber.closed == [(session.fingerprint, "scheme_mismatch")]
    assert len(store) == 0


def test_touch_counts_a_use_of_a_live_session(store: MemorySessionStore, clock: Clock) -> None:
    session_id, session = store.create("https")
    clock.advance(600)

    touched = store.touch(session.fingerprint)

    assert touched is not None and touched.last_used == 1600.0
    clock.advance(IDLE_SECONDS - 1)
    assert same(store.peek(session_id, "https"), session)  # the idle limit moved


def test_touch_never_revives_an_expired_or_unknown_session(
    store: MemorySessionStore, clock: Clock
) -> None:
    subscriber = Subscriber()
    store.on_close(subscriber)
    _, session = store.create("https")
    clock.advance(IDLE_SECONDS)

    assert store.touch(session.fingerprint) is None
    assert store.touch("0" * 64) is None
    assert subscriber.closed == [(session.fingerprint, "expired")]
    assert len(store) == 0


def test_get_is_peek_then_touch(store: MemorySessionStore, clock: Clock) -> None:
    session_id, _ = store.create("https")
    clock.advance(600)

    found = store.get(session_id, "https")

    assert found is not None and found.last_used == 1600.0


# D. A BaseException in a subscriber: everyone is still told, then it is raised again.


def test_a_cancelled_subscriber_still_lets_the_others_hear_and_is_raised_at_the_end(
    store: MemorySessionStore, caplog: pytest.LogCaptureFixture
) -> None:
    session_id, session = store.create("https")
    before, after = Subscriber(), Subscriber()

    def cancelled(key: str, reason: CloseReason) -> None:
        raise asyncio.CancelledError()

    store.on_close(before)
    store.on_close(cancelled)
    store.on_close(broken)
    store.on_close(after)

    with pytest.raises(asyncio.CancelledError):
        store.delete(session_id, "logout")

    assert before.closed == after.closed == [(session.fingerprint, "logout")]
    assert len(store) == 0
    assert store.get(session_id, "https") is None
    assert "session close subscriber failed: RuntimeError" in caplog.text
    assert session.fingerprint not in caplog.text


def test_only_the_first_base_exception_is_raised(store: MemorySessionStore) -> None:
    session_id, _ = store.create("https")
    told = Subscriber()

    def interrupted(key: str, reason: CloseReason) -> None:
        raise KeyboardInterrupt()

    def cancelled(key: str, reason: CloseReason) -> None:
        raise asyncio.CancelledError()

    store.on_close(interrupted)
    store.on_close(cancelled)
    store.on_close(told)

    with pytest.raises(KeyboardInterrupt):
        store.delete(session_id, "logout")
    assert len(told.closed) == 1
