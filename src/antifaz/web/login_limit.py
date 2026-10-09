"""Failed panel logins per client address (ADR-0018). Standard library only.

At most 5 failures in any 60 seconds per address (a sliding window, not fixed minutes); past
that, the login answers 429 with Retry-After until the oldest failure leaves the window. There
is NO global limit: with a 256-bit token it protects nothing and would let anyone lock the
administrator out.

IPv4 clients count per address; IPv6 clients per /64, since one machine usually holds a whole
/64 and could otherwise try with a new address each time. Anything that is not an IP
("testclient", "unknown") counts as written.

Bounded in memory and time: each address keeps at most 5 failure times, and the table at most
10 000 addresses, ordered by last failure. When it is full, expired entries are popped from the
front, then the entry with the oldest last failure: O(1) amortised per failure. A full table
never blocks a login: a new address is always allowed.

Concurrency: no lock. It is correct only while every call runs on the same event loop thread
with no await in the middle of a call, so the 11b-2 login route must be `async def`.

The address comes from forwarded.client_ip(). Forbidden: logging the addresses.
"""

import ipaddress
import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable

MAX_FAILURES = 5
WINDOW_SECONDS = 60.0
MAX_ADDRESSES = 10_000
IPV6_BUCKET_PREFIX = 64


def bucket(ip: str) -> str:
    """What the limit counts: an IPv4 address, the /64 of an IPv6 one, else the text itself."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.IPv6Network((address, IPV6_BUCKET_PREFIX), strict=False))
    return str(address)


class FailureLimiter:
    def __init__(
        self,
        max_failures: int = MAX_FAILURES,
        window: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        max_ips: int = MAX_ADDRESSES,
    ) -> None:
        if max_failures < 1 or window <= 0 or max_ips < 1:
            raise ValueError("max_failures, window and max_ips must be positive")
        self._max_failures = max_failures
        self._window = window
        self._clock = clock
        self._max_ips = max_ips
        # bucket -> times of its last failures (at most max_failures); least recent failure first.
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._failures)

    def allowed(self, ip: str) -> tuple[bool, int]:
        """(True, 0) if `ip` may try to log in now; else (False, seconds to wait, rounded up)."""
        now = self._clock()
        times = self._recent(bucket(ip), now)
        if times is None or len(times) < self._max_failures:
            return True, 0
        wait = times[0] + self._window - now
        return False, max(1, math.ceil(wait))

    def record_failure(self, ip: str) -> None:
        now = self._clock()
        key = bucket(ip)
        times = self._recent(key, now)
        if times is None:
            if len(self._failures) >= self._max_ips:
                self._make_room(now)
            times = self._failures[key] = deque(maxlen=self._max_failures)
        times.append(now)  # a full deque drops its oldest time: the window stays right
        self._failures.move_to_end(key)

    def _expired(self, times: deque[float], now: float) -> bool:
        return not times or now - times[-1] >= self._window

    def _recent(self, key: str, now: float) -> deque[float] | None:
        """The failures of `key` still in the window (older ones dropped); None if none."""
        times = self._failures.get(key)
        if times is None:
            return None
        while times and now - times[0] >= self._window:
            times.popleft()
        if not times:
            del self._failures[key]
            return None
        return times

    def _make_room(self, now: float) -> None:
        """Pop expired entries from the front (ordered by last failure), then the oldest."""
        while self._failures:
            times = next(iter(self._failures.values()))
            if not self._expired(times, now):
                break
            self._failures.popitem(last=False)
        while len(self._failures) >= self._max_ips:
            self._failures.popitem(last=False)
