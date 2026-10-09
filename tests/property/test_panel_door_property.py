"""The panel door's pure pieces hold their rules whatever the input (issue 53, ADR-0018).

- client_ip(): the peer, or an X-Forwarded-For entry that is not a trusted proxy, never one left
  of the first untrusted entry from the right, never a trusted address unless it is the peer.
- effective_scheme(): from an untrusted peer it is always scope["scheme"].
- Sessions: valid exactly while younger than 8 h and used in the last 30 min.
- Login limit: never more than 5 failures per address in any 60 s window, and never a refusal
  while there are fewer.
"""

import ipaddress
import math
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from antifaz.web.forwarded import (
    MAX_HOPS,
    IPNetwork,
    _entry_address,
    client_ip,
    effective_scheme,
    parse_networks,
)
from antifaz.web.login_limit import FailureLimiter
from antifaz.web.sessions import IDLE_SECONDS, MAX_AGE_SECONDS, MemorySessionStore

TRUSTED_CHOICES = ["10.0.0.0/24", "192.168.7.0/24", "fd00::/64", "203.0.113.5"]
TRUSTED_IPS = ["10.0.0.1", "10.0.0.200", "192.168.7.9", "fd00::9", "203.0.113.5", "::ffff:10.0.0.3"]
UNTRUSTED_IPS = ["198.51.100.7", "1.1.1.1", "2001:db8::1", "10.0.1.1", "203.0.113.6"]
# Entries a proxy may write with a port, and real garbage.
WITH_PORTS = ["10.0.0.1:80", "[fd00::9]:1", "198.51.100.7:443", "[2001:db8::2]:8443", "[::1]"]
GARBAGE = ["", " ", "unknown", "1.2.3.4:0", "[::1", "fe80::1%eth0", "x", "1.2.3", "_h"]

entries = st.lists(st.sampled_from(TRUSTED_IPS + UNTRUSTED_IPS + WITH_PORTS + GARBAGE), max_size=20)
trusted_sets = st.lists(st.sampled_from(TRUSTED_CHOICES), unique=True).map(parse_networks)
peers = st.sampled_from([*TRUSTED_IPS, *UNTRUSTED_IPS, "testclient"])


def _scope(peer: str, headers: list[tuple[bytes, bytes]], scheme: str = "http") -> dict[str, Any]:
    return {"type": "http", "scheme": scheme, "client": (peer, 1), "headers": headers}


def _address(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if "%" in text:
        return None
    try:
        address = ipaddress.ip_address(text.strip(" \t"))
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _trusted(text: str, trusted: tuple[IPNetwork, ...]) -> bool:
    address = _address(text)
    return address is not None and any(address in network for network in trusted)


def _entry_trusted(entry: str, trusted: tuple[IPNetwork, ...]) -> bool:
    address = _entry_address(entry)
    return address is not None and any(address in network for network in trusted)


@settings(max_examples=2000, deadline=None)
@given(peer=peers, headers=st.lists(entries, max_size=3), trusted=trusted_sets)
def test_client_ip_is_the_peer_or_the_first_untrusted_entry_from_the_right(
    peer: str, headers: list[list[str]], trusted: tuple[IPNetwork, ...]
) -> None:
    raw = [(b"x-forwarded-for", ", ".join(values).encode()) for values in headers]
    result = client_ip(_scope(peer, raw), trusted)

    peer_address = _address(peer)
    peer_text = peer if peer_address is None else str(peer_address)
    if not _trusted(peer, trusted):
        assert result == peer_text  # X-Forwarded-For from anyone else is ignored
        return
    joined = ",".join(", ".join(values) for values in headers).split(",") if headers else []
    # What the walk may look at: non-empty entries, at most MAX_HOPS from the right.
    walked = [entry.strip(" \t") for entry in reversed(joined) if entry.strip(" \t")][:MAX_HOPS]
    first_untrusted = next((e for e in walked if not _entry_trusted(e, trusted)), None)
    if result == peer_text:
        # Every entry walked was trusted, or the first one that was not is not an address.
        assert first_untrusted is None or _entry_address(first_untrusted) is None
        return
    assert first_untrusted is not None
    assert result == str(_entry_address(first_untrusted))  # never further left
    assert not _trusted(result, trusted)


@settings(max_examples=1000, deadline=None)
@given(
    peer=st.sampled_from([*UNTRUSTED_IPS, "testclient"]),
    scheme=st.sampled_from(["http", "https"]),
    values=st.lists(st.sampled_from(["https", "http", "HTTPS", "https, http", ""]), max_size=3),
    trusted=trusted_sets,
)
def test_effective_scheme_from_an_untrusted_peer_is_the_connection_scheme(
    peer: str, scheme: str, values: list[str], trusted: tuple[IPNetwork, ...]
) -> None:
    headers = [(b"x-forwarded-proto", value.encode()) for value in values]
    assert effective_scheme(_scope(peer, headers, scheme), trusted) == scheme


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@settings(max_examples=500, deadline=None)
@given(
    steps=st.lists(
        st.tuples(st.floats(min_value=0, max_value=3 * 3600, allow_nan=False), st.booleans()),
        max_size=40,
    )
)
def test_a_session_is_valid_exactly_while_young_and_recently_used(
    steps: list[tuple[float, bool]],
) -> None:
    clock = Clock()
    store = MemorySessionStore(clock=clock)
    session_id, session = store.create()
    created = last_used = clock.now
    alive = True
    for advance, use in steps:
        clock.now += advance
        expected = (
            alive and clock.now - created < MAX_AGE_SECONDS and clock.now - last_used < IDLE_SECONDS
        )
        if use:
            found = store.get(session_id)
            assert (found is not None and found.fingerprint == session.fingerprint) == expected
            if expected:
                last_used = clock.now
        else:
            assert store.is_active(session.fingerprint) == expected
        alive = expected


@settings(max_examples=500, deadline=None)
@given(
    attempts=st.lists(
        st.tuples(
            st.sampled_from(["192.0.2.1", "192.0.2.2", "192.0.2.3"]),
            st.floats(min_value=0, max_value=40, allow_nan=False),
        ),
        max_size=120,
    )
)
def test_the_limiter_never_accepts_more_than_five_failures_a_minute(
    attempts: list[tuple[str, float]],
) -> None:
    clock = Clock()
    limiter = FailureLimiter(clock=clock)
    recorded: dict[str, list[float]] = {}
    for ip, advance in attempts:
        clock.now += advance
        times = recorded.setdefault(ip, [])
        in_window = [t for t in times if clock.now - t < 60]
        allowed, retry_after = limiter.allowed(ip)
        assert allowed == (len(in_window) < 5)
        if allowed:
            assert retry_after == 0
            limiter.record_failure(ip)  # every attempt fails
            times.append(clock.now)
        else:
            assert 1 <= retry_after <= 60
    for times in recorded.values():
        for i in range(len(times) - 5):
            assert times[i + 5] - times[i] >= 60


@settings(max_examples=500, deadline=None)
@given(
    attempts=st.lists(
        st.tuples(
            st.sampled_from(["192.0.2.1", "192.0.2.2", "2001:db8::1", "2001:db8::2"]),
            st.floats(min_value=0, max_value=30, allow_nan=False),
        ),
        max_size=120,
    )
)
def test_the_limiter_stays_right_when_failures_are_recorded_while_blocked(
    attempts: list[tuple[str, float]],
) -> None:
    """A caller that records every failure, blocked or not: still blocked exactly while the
    last 5 failures of the bucket are in the window, and never more than 5 times kept."""
    clock = Clock()
    limiter = FailureLimiter(clock=clock)
    recorded: dict[str, list[float]] = {}
    for ip, advance in attempts:
        clock.now += advance
        key = "2001:db8::/64" if ":" in ip else ip  # both IPv6 addresses share one /64
        times = recorded.setdefault(key, [])
        in_window = [t for t in times if clock.now - t < 60]
        allowed, retry_after = limiter.allowed(ip)
        assert allowed == (len(in_window) < 5)
        if not allowed:
            assert retry_after == max(1, math.ceil(in_window[-5] + 60 - clock.now))
        limiter.record_failure(ip)
        times.append(clock.now)
    assert all(len(kept) <= 5 for kept in limiter._failures.values())
