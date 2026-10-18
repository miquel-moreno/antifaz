"""Speed tests that do not depend on how busy the machine is (ADR-0008).

A fixed limit in seconds of wall time ("scan(text) takes < 2 s") fails on a loaded laptop or
under coverage and passes a quadratic regex on a fast machine. These helpers measure CPU time:

- `assert_linear` checks the SHAPE: the CPU time of the same work for size n and for 4n. Linear
  work takes about 4 times as long, quadratic work 16 times, catastrophic backtracking far
  more; the assertion is a ratio below 8.
- `cpu_budget` is a loose absolute limit in CPU seconds, for a DoS test whose input has a fixed
  size (the 4 MiB body): a linear but 10 times slower path is still caught there.

About the measure:

- CPU time (`time.process_time`), not wall time: waiting for the CPU on a loaded machine does
  not count. It covers every thread of this process, which matters for the proxy tests (the
  TestClient runs the app in another thread); it does not cover child processes, and nothing
  measured with it runs in one (the NER pool is tested by facts, see test_pool.py).
- Every run gets a fresh input from `prepare(n)`: stateful work (a table that fills up, a
  cache) is never measured warm.
- n calibrates itself: it doubles until the best run takes at least `min_seconds` (0.1 s), so
  the ratio is not noise (process_time ticks every ~16 ms on Windows) on any machine; it gives
  up after 20 doublings.
- Best of `repeats` runs for each size: noise only ever adds time. The 4n run is not repeated
  when it already passes, when its ratio is above twice the limit (no noise brings that below
  the limit) or when it takes more than `long_seconds`.
- A hang guard (120 s of wall time per run) is checked when a run returns: a run that finishes
  but took longer fails with a clear message. It cannot stop a run that never returns (that
  needs a per-test timeout, such as pytest-timeout, which the project does not use).
"""

import gc
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

MAX_RATIO = 8.0
MIN_SECONDS = 0.1
REPEATS = 3
LONG_SECONDS = 5.0
MAX_DOUBLINGS = 20
HANG_GUARD_SECONDS = 120.0

# (what, CPU seconds used, limit) of every cpu_budget block, printed in the run's summary
CPU_SPENT: list[tuple[str, float, float]] = []

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
    """Fail if the block finished but took more than `seconds` of wall time.

    Checked when the block returns, so it cannot stop a block that never does; it is not a
    speed measure either, only a clear message for a run that came close to hanging."""
    began = time.perf_counter()
    yield
    wall = time.perf_counter() - began
    if wall > seconds:
        raise AssertionError(
            f"hang guard: it finished but took {wall:.1f} s of wall time (limit {seconds:.0f} s). "
            "This is not a speed measure, only a guard against a hang."
        )


@contextmanager
def cpu_budget(
    seconds: float, what: str, guard_seconds: float = HANG_GUARD_SECONDS
) -> Iterator[None]:
    """Fail if the block used more than `seconds` of CPU time (every thread of this process).

    A loose absolute limit for work of a fixed size: waiting on a busy machine does not count,
    so the limit can stay closer to what the work really costs than a wall-time one. Leave room
    for coverage and for a machine with every core busy (a lower clock and shared cores made the
    same work use about twice the CPU time)."""
    with hang_guard(guard_seconds):
        began = time.process_time()
        yield
        spent = time.process_time() - began
    CPU_SPENT.append((what, spent, seconds))
    if spent > seconds:
        raise AssertionError(
            f"{what} took {spent:.1f} s of CPU time (limit {seconds:g} s; CPU time, so waiting "
            "on a busy machine does not count)"
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
    long_seconds: float = LONG_SECONDS,
    clock: Callable[[], float] = time.process_time,
    guard_seconds: float = HANG_GUARD_SECONDS,
) -> Shape:
    """Time `prepare(n)()` and `prepare(4 * n)()`; only the returned callable is timed, and
    each run gets its own `prepare` (building the input stays outside the measure).

    n starts at `start` and doubles until the BEST of `repeats` runs takes `min_seconds` of CPU
    (a first run is often slower: caches, compiled patterns), as long as 4n stays within `limit`
    (when the input has a maximum size, such as the body limit).
    """
    if start < 1 or repeats < 1 or min_seconds <= 0:
        raise ValueError("start, repeats and min_seconds must be positive")
    if limit is not None and 4 * start > limit:
        raise ValueError("4 * start must fit in limit")

    def run(size: int) -> float:
        return _run_once(prepare(size), clock, guard_seconds)

    n = start
    doublings = 0

    def grow() -> bool:
        nonlocal n, doublings
        if limit is not None and 8 * n > limit:
            return False
        if doublings == MAX_DOUBLINGS:
            raise AssertionError(
                f"the work for n={n} still took under {min_seconds} s of CPU after "
                f"{MAX_DOUBLINGS} doublings; give it a larger start"
            )
        n *= 2
        doublings += 1
        return True

    small = run(n)
    while True:
        while small < min_seconds and grow():  # one run per size while it is clearly short
            small = run(n)
        for _ in range(repeats - 1):
            small = min(small, run(n))
        if small >= min_seconds or not grow():
            break
        small = run(n)
    if small <= 0:
        raise AssertionError(
            f"the work for n={n} took no measurable CPU time; give it a larger start or limit"
        )
    large = run(4 * n)
    for _ in range(repeats - 1):
        ratio = large / small
        if ratio < MAX_RATIO or ratio > 2 * MAX_RATIO or large > long_seconds:
            break  # it passes (more runs only lower it), it cannot pass, or it is too long
        large = min(large, run(4 * n))
    return Shape(n, small, large)


def assert_linear(
    prepare: Callable[[int], Work],
    *,
    start: int,
    limit: int | None = None,
    min_seconds: float = MIN_SECONDS,
    repeats: int = REPEATS,
    long_seconds: float = LONG_SECONDS,
    clock: Callable[[], float] = time.process_time,
    guard_seconds: float = HANG_GUARD_SECONDS,
) -> Shape:
    """Fail if the work grows clearly faster than its input (see `measure_shape`)."""
    shape = measure_shape(
        prepare,
        start=start,
        limit=limit,
        min_seconds=min_seconds,
        repeats=repeats,
        long_seconds=long_seconds,
        clock=clock,
        guard_seconds=guard_seconds,
    )
    assert shape.ratio < MAX_RATIO, (
        f"grows faster than linear: n={shape.n} took {shape.small * 1000:.0f} ms of CPU, 4n took "
        f"{shape.large * 1000:.0f} ms (x{shape.ratio:.1f}; linear is about x4, quadratic x16, "
        f"the limit is x{MAX_RATIO:.0f})"
    )
    return shape
