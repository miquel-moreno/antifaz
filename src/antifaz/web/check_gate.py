"""The gate of «Comprobar ahora» (issue 53, PR 11e-4, ADR-0018). Standard library only.

One provider check at a time per process, and a cooldown of COOLDOWN_SECONDS (30 s) counted from
the END of the last check, so a slow check never shortens the wait. Per process: with several
worker processes each has its own gate (documented in TECNICO).

- begin(now): None if a check may start now (it is taken); else ("busy", 1) while one runs, or
  ("cooldown", seconds) with the whole seconds left, rounded up (never 0). A refused begin()
  changes nothing.
- end(now): the check is over (the route calls it in a `finally`, whatever happened) and the
  cooldown starts.
- now(): the injected clock (time.monotonic by default; tests set `clock`).

Concurrency: no lock. Correct only while every call runs on the event loop thread with no await
inside a call: the route is `async def` and takes the gate before its first await
(tests/unit/test_web_concurrency.py).
"""

import math
import time
from collections.abc import Callable
from typing import Literal

COOLDOWN_SECONDS = 30.0
BUSY_RETRY_SECONDS = 1

type Refusal = tuple[Literal["busy", "cooldown"], int]


class CheckGate:
    def __init__(
        self, cooldown: float = COOLDOWN_SECONDS, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if not cooldown >= 0:  # also refuses NaN
            raise ValueError("the cooldown must be zero or more seconds")
        self.cooldown = cooldown
        self.clock = clock
        self.running = False
        self._ended: float | None = None

    def now(self) -> float:
        return self.clock()

    def begin(self, now: float) -> Refusal | None:
        """Take the check, or say why not and for how many whole seconds."""
        if self.running:
            return "busy", BUSY_RETRY_SECONDS
        if self._ended is not None:
            left = self._ended + self.cooldown - now
            if left > 0:
                return "cooldown", max(1, math.ceil(left))
        self.running = True
        return None

    def end(self, now: float) -> None:
        """The check is over: the cooldown counts from `now`."""
        self.running = False
        self._ended = now
