"""The «Prueba un texto» rate limit (issue 53, PR 11d-a, ADR-0018). Standard library only.

At most 30 tries in any 60 seconds per panel session (a sliding window, not fixed minutes). The
key is the session's fingerprint (sessions.py), which only the server computes: a client can
never pick its bucket. Every try counts, whatever its body: the route calls take() BEFORE it
reads the body, so a refused request costs no reading, no parsing and no thread.

take() checks and counts in one plain call: 0 means allowed (and counted); anything else is the
whole seconds to wait, rounded up, and nothing is counted. forget() drops a session's entry; the
panel wires it to SessionStore.on_close, so a closed session (logout, a new login, expiry,
eviction) leaves nothing behind.

Bounded in memory and time: each key keeps at most MAX_TRIES times, the table at most MAX_KEYS
keys, ordered by last try. When it is full, expired entries are popped from the front, then the
entry with the oldest last try. A full table never refuses a new session (the store holds 32
sessions at most, so it never fills in practice).

Concurrency: no lock. Correct only while every call runs on the event loop thread with no await
inside a call: the try route is `async def` and nothing awaits between take() and its return
(tests/unit/test_web_concurrency.py).

Forbidden: keying by anything the client sends (a cookie value, an address, a header). Logging
a key.
"""

import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable

MAX_TRIES = 30
WINDOW_SECONDS = 60.0
MAX_KEYS = 256


class TryLimiter:
    def __init__(
        self,
        max_tries: int = MAX_TRIES,
        window: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = MAX_KEYS,
    ) -> None:
        if max_tries < 1 or window <= 0 or max_keys < 1:
            raise ValueError("max_tries, window and max_keys must be positive")
        self._max_tries = max_tries
        self._window = window
        self._clock = clock
        self._max_keys = max_keys
        # key -> times of its last tries (at most max_tries); least recent try first.
        self._tries: OrderedDict[str, deque[float]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._tries)

    def take(self, key: str) -> int:
        """0 if `key` may try now (the try is counted); else the seconds to wait (rounded up)."""
        now = self._clock()
        times = self._recent(key, now)
        if times is not None and len(times) >= self._max_tries:
            return max(1, math.ceil(times[0] + self._window - now))
        if times is None:
            if len(self._tries) >= self._max_keys:
                self._make_room(now)
            times = self._tries[key] = deque(maxlen=self._max_tries)
        times.append(now)
        self._tries.move_to_end(key)
        return 0

    def forget(self, key: str) -> None:
        """Drop the entry of `key` (its session closed). Nothing happens if there is none."""
        self._tries.pop(key, None)

    def _recent(self, key: str, now: float) -> deque[float] | None:
        """The tries of `key` still in the window (older ones dropped); None if none."""
        times = self._tries.get(key)
        if times is None:
            return None
        while times and now - times[0] >= self._window:
            times.popleft()
        if not times:
            del self._tries[key]
            return None
        return times

    def _make_room(self, now: float) -> None:
        """Pop expired entries from the front (ordered by last try), then the oldest."""
        while self._tries:
            times = next(iter(self._tries.values()))
            if times and now - times[-1] < self._window:
                break
            self._tries.popitem(last=False)
        while len(self._tries) >= self._max_keys:
            self._tries.popitem(last=False)
