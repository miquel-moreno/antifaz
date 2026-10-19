"""The «Prueba un texto» rate limit (issue 53, PR 11d-a, ADR-0018): web/try_limit.py.

At most 30 tries in any 60 seconds per session (a sliding window), keyed by the session's
fingerprint, which only the server computes. take() checks and counts in one plain call: 0 means
allowed and counted, anything else is the whole seconds to wait (nothing counted). The table is
bounded and an entry goes when its session closes (forget(), wired to SessionStore.on_close).
"""

import pytest

from antifaz.web.try_limit import MAX_KEYS, MAX_TRIES, WINDOW_SECONDS, TryLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_defaults_are_the_approved_ones() -> None:
    assert (MAX_TRIES, WINDOW_SECONDS) == (30, 60.0)
    assert MAX_KEYS >= 64


def test_thirty_tries_then_a_wait() -> None:
    clock = Clock()
    limiter = TryLimiter(clock=clock)

    assert [limiter.take("a") for _ in range(30)] == [0] * 30
    assert limiter.take("a") == 60
    clock.now += 59.2
    assert limiter.take("a") == 1  # rounded up, never 0 while refused


def test_the_window_slides() -> None:
    clock = Clock()
    limiter = TryLimiter(max_tries=3, window=10.0, clock=clock)
    for step in (0.0, 4.0, 8.0):
        clock.now = 1000.0 + step
        assert limiter.take("a") == 0

    clock.now = 1009.0
    assert limiter.take("a") == 1
    clock.now = 1010.0  # the first try left the window
    assert limiter.take("a") == 0
    assert limiter.take("a") == 4  # the one at 1004 leaves at 1014


def test_a_refused_try_does_not_count() -> None:
    clock = Clock()
    limiter = TryLimiter(max_tries=2, window=10.0, clock=clock)
    limiter.take("a")
    limiter.take("a")
    for _ in range(50):
        assert limiter.take("a") > 0

    clock.now += 10.0
    assert limiter.take("a") == 0


def test_sessions_are_counted_apart() -> None:
    limiter = TryLimiter(max_tries=1, clock=Clock())

    assert limiter.take("a") == 0
    assert limiter.take("a") > 0
    assert limiter.take("b") == 0


def test_forget_drops_the_entry() -> None:
    limiter = TryLimiter(max_tries=1, clock=Clock())
    limiter.take("a")
    assert len(limiter) == 1

    limiter.forget("a")
    limiter.forget("never-there")

    assert len(limiter) == 0
    assert limiter.take("a") == 0


def test_the_table_is_bounded_and_a_new_session_is_always_allowed() -> None:
    clock = Clock()
    limiter = TryLimiter(max_tries=1, window=60.0, clock=clock, max_keys=3)
    for key in ("a", "b", "c"):
        limiter.take(key)
        clock.now += 1

    assert limiter.take("d") == 0
    assert len(limiter) == 3
    assert limiter.take("a") == 0  # the oldest went to make room


def test_expired_entries_go_first() -> None:
    clock = Clock()
    limiter = TryLimiter(max_tries=1, window=10.0, clock=clock, max_keys=2)
    limiter.take("old")
    clock.now += 11
    limiter.take("recent")

    assert limiter.take("new") == 0
    assert limiter.take("recent") > 0  # kept: only the expired one went


def test_entries_expire_on_their_own() -> None:
    clock = Clock()
    limiter = TryLimiter(max_tries=1, window=10.0, clock=clock)
    limiter.take("a")
    clock.now += 10

    assert limiter.take("a") == 0
    assert len(limiter) == 1


@pytest.mark.parametrize(
    "arguments", [{"max_tries": 0}, {"window": 0.0}, {"max_keys": 0}, {"window": -1.0}]
)
def test_nonsense_limits_are_refused(arguments: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        TryLimiter(**arguments)  # type: ignore[arg-type]
