"""Speed tests that do not depend on how busy the machine is (ADR-0008).

A fixed limit in seconds ("scan(text) takes < 2 s") fails on a loaded laptop or under coverage
and passes a quadratic regex on a fast machine. These helpers measure the SHAPE instead: the CPU
time of the same work for size n and for 4n. Linear work takes about 4 times as long, quadratic
work 16 times, catastrophic backtracking far more; the assertion is a ratio below 8.

- CPU time (`time.process_time`), not wall time: waiting for the CPU on a loaded machine does
  not count. It covers every thread of this process, which matters for the proxy tests (the
  TestClient runs the app in another thread); it does not cover child processes, and nothing
  measured with it runs in one (the NER pool is tested by facts, see test_pool.py).
- n calibrates itself: it doubles until the best run takes at least `min_seconds` (0.1 s), so
  the ratio is not noise (process_time ticks every ~16 ms on Windows) on any machine.
- Best of `repeats` runs for each size: noise only ever adds time.
- The only limit in seconds is a hang guard (120 s of wall time for one run): it is there so a
  real hang ends with a clear message, not to measure speed.
"""

import gc
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

MAX_RATIO = 8.0
MIN_SECONDS = 0.1
REPEATS = 3
HANG_GUARD_SECONDS = 120.0

Work = Callable[[], object]


@dataclass(frozen=True, slots=True)
class Shape:
    """CPU seconds of the best run for size `n` and for size `4 * n`."""

    n: int
    small: float
    large: float

    @property
    def ratio(self) -> float:
        return self.large / self.small


@contextmanager
def hang_guard(seconds: float = HANG_GUARD_SECONDS) -> Iterator[None]:
    """Fail if the block took more than `seconds` of wall time. Not a speed measure: a test
    that must not hang, and whose speed is measured elsewhere, keeps only this."""
    began = time.perf_counter()
    yield
    wall = time.perf_counter() - began
    if wall > seconds:
        raise AssertionError(
            f"hang guard: it took {wall:.1f} s of wall time (limit {seconds:.0f} s). "
            "This is not a speed measure, only a guard against a hang."
        )


def _run_once(work: Work, clock: Callable[[], float], guard: float) -> float:
    enabled = gc.isenabled()
    gc.disable()  # no collector pause lands inside one run and not the other
    try:
        with hang_guard(guard):
            began = clock()
            work()
            return clock() - began
    finally:
        if enabled:
            gc.enable()


def measure_shape(
    prepare: Callable[[int], Work],
    *,
    start: int,
    limit: int | None = None,
    min_seconds: float = MIN_SECONDS,
    repeats: int = REPEATS,
    clock: Callable[[], float] = time.process_time,
    guard_seconds: float = HANG_GUARD_SECONDS,
) -> Shape:
    """Time `prepare(n)()` and `prepare(4 * n)()`; only the returned callable is timed.

    n starts at `start` and doubles until the BEST of `repeats` runs takes `min_seconds` of CPU
    (a first run is often slower: caches, compiled patterns), as long as 4n stays within `limit`
    (when the input has a maximum size, such as the body limit). Building the input happens in
    `prepare`, outside the measure.
    """
    if start < 1 or repeats < 1 or min_seconds <= 0:
        raise ValueError("start, repeats and min_seconds must be positive")
    if limit is not None and 4 * start > limit:
        raise ValueError("4 * start must fit in limit")

    def can_grow() -> bool:
        return limit is None or 8 * n <= limit

    n = start
    work = prepare(n)
    small = _run_once(work, clock, guard_seconds)
    while True:
        while small < min_seconds and can_grow():  # one run per size while it is clearly short
            n *= 2
            work = prepare(n)
            small = _run_once(work, clock, guard_seconds)
        for _ in range(repeats - 1):
            small = min(small, _run_once(work, clock, guard_seconds))
        if small >= min_seconds or not can_grow():
            break
        n *= 2
        work = prepare(n)
        small = _run_once(work, clock, guard_seconds)
    if small <= 0:
        raise AssertionError(
            f"the work for n={n} took no measurable CPU time; give it a larger start or limit"
        )
    work = prepare(4 * n)
    large = _run_once(work, clock, guard_seconds)
    for _ in range(repeats - 1):
        if large / small < MAX_RATIO:
            break  # more runs could only lower it
        large = min(large, _run_once(work, clock, guard_seconds))
    return Shape(n, small, large)


def assert_linear(prepare: Callable[[int], Work], **options: object) -> Shape:
    """Fail if the work grows clearly faster than its input (see `measure_shape`)."""
    shape = measure_shape(prepare, **options)  # type: ignore[arg-type]  # keyword pass-through
    assert shape.ratio < MAX_RATIO, (
        f"grows faster than linear: n={shape.n} took {shape.small * 1000:.0f} ms of CPU, 4n took "
        f"{shape.large * 1000:.0f} ms (x{shape.ratio:.1f}; linear is about x4, quadratic x16, "
        f"the limit is x{MAX_RATIO:.0f})"
    )
    return shape
