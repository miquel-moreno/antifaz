"""The gate of «Comprobar ahora» (issue 53, PR 11e-4): signatures only, to be implemented."""

import time
from collections.abc import Callable

COOLDOWN_SECONDS = 0.0


class CheckGate:
    def __init__(self, cooldown: float = 30.0, clock: Callable[[], float] = time.monotonic) -> None:
        raise NotImplementedError
