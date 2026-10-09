"""Failed panel logins per client address (ADR-0018). Standard library only.

At most 5 failures in any 60 seconds per address (a sliding window, not fixed minutes); past
that, the login answers 429 with Retry-After until the oldest failure leaves the window. There
is NO global limit: with a 256-bit token it protects nothing and would let anyone lock the
administrator out.

The table is bounded (10 000 addresses). When it is full, expired entries go first and then the
entry whose last failure is oldest. A full table never blocks a login: a new address is always
allowed.

The address comes from forwarded.client_ip(). Forbidden: logging the addresses.
"""

import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable

MAX_FAILURES = 5
WINDOW_SECONDS = 60.0
MAX_ADDRESSES = 10_000


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
        # address -> times of its failures still in the window; the least recent failure first.
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._failures)

    def allowed(self, ip: str) -> tuple[bool, int]:
        """(True, 0) if `ip` may try to log in now; else (False, seconds to wait, rounded up)."""
        now = self._clock()
        times = self._recent(ip, now)
        if times is None or len(times) < self._max_failures:
            return True, 0
        wait = times[0] + self._window - now
        return False, max(1, math.ceil(wait))

    def record_failure(self, ip: str) -> None:
        now = self._clock()
        times = self._recent(ip, now)
        if times is None:
            if len(self._failures) >= self._max_ips:
                self._make_room(now)
            times = self._failures[ip] = deque()
        times.append(now)
        self._failures.move_to_end(ip)

    def _recent(self, ip: str, now: float) -> deque[float] | None:
        """The failures of `ip` still in the window (older ones dropped); None if there are none."""
        times = self._failures.get(ip)
        if times is None:
            return None
        while times and now - times[0] >= self._window:
            times.popleft()
        if not times:
            del self._failures[ip]
            return None
        return times

    def _make_room(self, now: float) -> None:
        expired = [ip for ip, times in self._failures.items() if now - times[-1] >= self._window]
        for ip in expired:
            del self._failures[ip]
        while len(self._failures) >= self._max_ips:
            self._failures.popitem(last=False)
