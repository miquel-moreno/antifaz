"""Failed panel logins per client address (issue 53, ADR-0018): web/login_limit.py.

A fake clock drives the window: no test sleeps. The addresses are documentation ranges.
"""

import time

import pytest

from antifaz.web.login_limit import FailureLimiter, bucket

IP = "198.51.100.7"
OTHER = "203.0.113.9"


class Clock:
    def __init__(self, now: float = 500.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def limiter(clock: Clock) -> FailureLimiter:
    return FailureLimiter(clock=clock)


def fail(limiter: FailureLimiter, ip: str, times: int) -> None:
    for _ in range(times):
        limiter.record_failure(ip)


def test_a_new_address_is_allowed(limiter: FailureLimiter) -> None:
    assert limiter.allowed(IP) == (True, 0)


def test_five_failures_are_allowed_and_the_sixth_attempt_is_blocked(
    limiter: FailureLimiter,
) -> None:
    for _ in range(5):
        assert limiter.allowed(IP) == (True, 0)
        limiter.record_failure(IP)

    allowed, retry_after = limiter.allowed(IP)
    assert not allowed
    assert retry_after == 60


def test_retry_after_is_rounded_up(limiter: FailureLimiter, clock: Clock) -> None:
    fail(limiter, IP, 1)
    clock.advance(0.4)
    fail(limiter, IP, 4)
    clock.advance(10.1)

    # The oldest failure leaves the window in 60 - 10.5 = 49.5 s.
    assert limiter.allowed(IP) == (False, 50)
    clock.advance(49.4)
    assert limiter.allowed(IP) == (False, 1)


def test_the_window_slides(limiter: FailureLimiter, clock: Clock) -> None:
    for _ in range(5):
        limiter.record_failure(IP)
        clock.advance(10)  # failures at 0, 10, 20, 30, 40; now 50

    assert limiter.allowed(IP) == (False, 10)
    clock.advance(10)  # the first one (at 0) leaves the window at 60
    assert limiter.allowed(IP) == (True, 0)
    limiter.record_failure(IP)
    assert limiter.allowed(IP) == (False, 10)  # the one at 10 leaves at 70


def test_after_a_quiet_minute_the_address_is_forgotten(
    limiter: FailureLimiter, clock: Clock
) -> None:
    fail(limiter, IP, 5)
    clock.advance(60)

    assert limiter.allowed(IP) == (True, 0)
    assert len(limiter) == 0


def test_addresses_are_independent(limiter: FailureLimiter) -> None:
    fail(limiter, IP, 5)

    assert not limiter.allowed(IP)[0]
    assert limiter.allowed(OTHER) == (True, 0)


def test_there_is_no_global_limit(limiter: FailureLimiter) -> None:
    for n in range(500):
        fail(limiter, f"10.{n // 256}.{n % 256}.1", 4)

    assert limiter.allowed(IP) == (True, 0)
    assert all(limiter.allowed(f"10.0.{n}.1")[0] for n in range(256))


def test_a_full_table_evicts_the_oldest_and_still_allows_a_new_address(clock: Clock) -> None:
    limiter = FailureLimiter(clock=clock, max_ips=3)
    fail(limiter, "192.0.2.1", 5)
    clock.advance(1)
    fail(limiter, "192.0.2.2", 5)
    clock.advance(1)
    fail(limiter, "192.0.2.3", 5)

    assert limiter.allowed(IP) == (True, 0)
    limiter.record_failure(IP)

    assert len(limiter) == 3
    assert limiter.allowed("192.0.2.1") == (True, 0)  # the oldest went
    assert not limiter.allowed("192.0.2.2")[0]
    assert not limiter.allowed("192.0.2.3")[0]


def test_a_full_table_drops_every_expired_entry_first(clock: Clock) -> None:
    limiter = FailureLimiter(clock=clock, max_ips=3)
    fail(limiter, "192.0.2.1", 1)
    fail(limiter, "192.0.2.2", 1)
    clock.advance(30)
    fail(limiter, "192.0.2.3", 5)
    clock.advance(31)  # .1 and .2 are out of the window; .3 is not

    limiter.record_failure(IP)

    assert len(limiter) == 2  # both expired entries went, the live one stayed
    assert not limiter.allowed("192.0.2.3")[0]


def test_the_least_recent_failure_is_evicted_first(clock: Clock) -> None:
    limiter = FailureLimiter(clock=clock, max_ips=2)
    fail(limiter, "192.0.2.1", 5)
    clock.advance(1)
    fail(limiter, "192.0.2.2", 5)
    clock.advance(1)
    limiter.record_failure("192.0.2.1")  # .1 failed again: .2 is now the oldest

    limiter.record_failure(IP)

    assert not limiter.allowed("192.0.2.1")[0]
    assert limiter.allowed("192.0.2.2") == (True, 0)


def test_a_table_of_ten_thousand_never_blocks_a_new_address(clock: Clock) -> None:
    limiter = FailureLimiter(clock=clock)
    for n in range(10_000):
        limiter.record_failure(f"10.{n // 65536}.{n // 256 % 256}.{n % 256}")
    assert len(limiter) == 10_000

    assert limiter.allowed(IP) == (True, 0)
    limiter.record_failure(IP)
    assert len(limiter) == 10_000


@pytest.mark.parametrize(
    "options", [{"max_failures": 0}, {"window": 0}, {"window": -1}, {"max_ips": 0}]
)
def test_bad_limits_are_refused(options: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        FailureLimiter(**options)  # type: ignore[arg-type]


def test_each_address_keeps_at_most_max_failures_times(limiter: FailureLimiter) -> None:
    fail(limiter, IP, 50)  # even if the caller keeps recording while blocked

    assert len(limiter._failures[IP]) == 5
    assert limiter.allowed(IP) == (False, 60)


def test_a_full_table_costs_little_per_new_address(clock: Clock) -> None:
    # Generous bound: the old linear scan took seconds here; this takes milliseconds.
    limiter = FailureLimiter(clock=clock)
    for n in range(10_000):
        limiter.record_failure(f"10.0.{n // 256}.{n % 256}")
    started = time.perf_counter()
    for n in range(10_000):
        limiter.record_failure(f"10.1.{n // 256}.{n % 256}")
    elapsed = time.perf_counter() - started

    assert len(limiter) == 10_000
    assert elapsed < 2.0


def test_ipv6_clients_count_per_64_and_ipv4_per_address(limiter: FailureLimiter) -> None:
    for host in range(1, 6):
        limiter.record_failure(f"2001:db8:1:2::{host:x}")  # five addresses, one /64

    assert not limiter.allowed("2001:db8:1:2:ffff::1")[0]
    assert limiter.allowed("2001:db8:1:3::1") == (True, 0)  # the next /64
    fail(limiter, "198.51.100.1", 5)
    assert limiter.allowed("198.51.100.2") == (True, 0)
    assert not limiter.allowed("::ffff:198.51.100.1")[0]  # mapped: the same IPv4 address


@pytest.mark.parametrize(
    ("ip", "expected"),
    [
        ("198.51.100.7", "198.51.100.7"),
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"),
        ("::ffff:198.51.100.7", "198.51.100.7"),
        ("testclient", "testclient"),
        ("unknown", "unknown"),
    ],
)
def test_bucket(ip: str, expected: str) -> None:
    assert bucket(ip) == expected
