"""The shape helper of the speed tests (tests/timing.py): it must fail on superlinear work.

The canaries are real code with a known shape: a regex that retries a long run from every
position (quadratic), one with catastrophic backtracking (exponential, kept tiny so it cannot
hang) and a quadratic pure-Python loop must fail; their linear twins must pass.
"""

import re
import time
from collections.abc import Callable

import pytest

from tests.timing import (
    CPU_SPENT,
    MAX_RATIO,
    Shape,
    Work,
    assert_linear,
    cpu_budget,
    hang_guard,
    measure_shape,
)


class FakeClock:
    """CPU time that only moves when the fake work says so."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _costing(
    clock: FakeClock, cost: Callable[[int], float], sizes: list[int]
) -> Callable[[int], Work]:
    def prepare(n: int) -> Work:
        sizes.append(n)

        def work() -> None:
            clock.now += cost(n)

        return work

    return prepare


# --- Deterministic: a fake clock ----------------------------------------------------------------


def test_linear_work_passes_and_n_doubles_until_the_minimum_time() -> None:
    clock = FakeClock()
    sizes: list[int] = []
    shape = assert_linear(_costing(clock, lambda n: n * 1e-4, sizes), start=10, clock=clock)
    assert shape.n == 1280  # 10, 20, ... until 0.128 s >= 0.1 s
    assert shape.ratio == pytest.approx(4.0)
    assert max(sizes) == 4 * 1280


def test_a_slow_first_run_does_not_stop_the_calibration_too_early() -> None:
    # The very first run costs 3 times as much (caches, compiled patterns): at n=640 it looks
    # long enough (0.192 s) but the best of 3 is 0.064 s.
    clock = FakeClock()
    runs: list[None] = []

    def prepare(n: int) -> Work:
        def work() -> None:
            clock.now += n * 1e-4 * (3 if not runs else 1)
            runs.append(None)

        return work

    shape = assert_linear(prepare, start=10, clock=clock)
    assert shape.n == 1280
    assert shape.small == pytest.approx(0.128)


def test_every_run_gets_a_fresh_input_so_stateful_work_is_never_measured_warm() -> None:
    # Work that is cheap once it has run (a table already full, a cache already warm) must not
    # be measured warm: each run gets its own prepare(n).
    clock = FakeClock()
    prepared: list[int] = []
    runs: list[int] = []

    def prepare(n: int) -> Work:
        prepared.append(n)
        done: list[None] = []

        def work() -> None:
            clock.now += 0.0 if done else n * 1e-4
            done.append(None)
            runs.append(n)

        return work

    shape = assert_linear(prepare, start=10, clock=clock)
    assert shape.small == pytest.approx(0.128)
    assert shape.ratio == pytest.approx(4.0)
    assert prepared == runs  # one fresh input per run, never a second run of the same one


def test_quadratic_work_fails() -> None:
    clock = FakeClock()
    with pytest.raises(AssertionError, match=r"grows faster than linear.*x16\.0"):
        assert_linear(_costing(clock, lambda n: n * n * 1e-6, []), start=10, clock=clock)


def test_a_ratio_just_below_the_limit_passes_and_just_above_fails() -> None:
    clock = FakeClock()
    below = lambda n: 0.1 * (n / 10) ** 1.49  # noqa: E731  # 4 ** 1.49 < 8
    assert_linear(_costing(clock, below, []), start=10, clock=clock)
    above = lambda n: 0.1 * (n / 10) ** 1.51  # noqa: E731  # 4 ** 1.51 > 8
    with pytest.raises(AssertionError):
        assert_linear(_costing(clock, above, []), start=10, clock=clock)


def test_the_limit_caps_the_size_even_below_the_minimum_time() -> None:
    clock = FakeClock()
    sizes: list[int] = []
    shape = measure_shape(
        _costing(clock, lambda n: n * 1e-6, sizes), start=10, limit=100, clock=clock
    )
    assert shape.n == 20  # 4 * 40 would pass the limit
    assert max(sizes) <= 100


def test_a_4n_far_above_the_limit_is_run_only_once() -> None:
    clock = FakeClock()
    sizes: list[int] = []
    with pytest.raises(AssertionError, match="grows faster than linear"):
        assert_linear(_costing(clock, lambda n: n * n * 1e-6, sizes), start=10, clock=clock)
    assert sizes.count(max(sizes)) == 1  # x16 > 2 * x8: more runs could not bring it below


def test_a_long_4n_run_is_not_repeated() -> None:
    clock = FakeClock()
    sizes: list[int] = []
    cost = lambda n: 0.15 * (n / 10) ** 1.6  # noqa: E731  # x9.2 at 4n, and 4n takes 1.4 s
    with pytest.raises(AssertionError):
        assert_linear(_costing(clock, cost, sizes), start=10, clock=clock, long_seconds=1.0)
    assert sizes.count(max(sizes)) == 1
    sizes.clear()
    with pytest.raises(AssertionError):
        assert_linear(_costing(clock, cost, sizes), start=10, clock=clock)  # 1.4 s < 5 s
    assert sizes.count(max(sizes)) == 3


def test_the_calibration_gives_up_after_20_doublings() -> None:
    clock = FakeClock()
    with pytest.raises(AssertionError, match="20 doublings"):
        measure_shape(_costing(clock, lambda n: n * 1e-12, []), start=1, clock=clock)


def test_work_that_takes_no_measurable_time_is_an_error() -> None:
    clock = FakeClock()
    with pytest.raises(AssertionError, match="no measurable CPU time"):
        measure_shape(_costing(clock, lambda n: 0.0, []), start=10, limit=40, clock=clock)


def test_the_hang_guard_says_it_is_a_hang_guard() -> None:
    message = r"hang guard: it finished but took .* not a speed measure"
    with pytest.raises(AssertionError, match=message):
        measure_shape(lambda n: lambda: time.sleep(0.01), start=1, guard_seconds=0.001)
    with pytest.raises(AssertionError, match=message), hang_guard(0.001):
        time.sleep(0.01)
    with hang_guard():
        pass


def test_the_cpu_budget_counts_cpu_not_waiting() -> None:
    with cpu_budget(0.5, "sleeping"):
        time.sleep(0.6)  # waiting, as on a busy machine: no CPU
    spinning = r"spinning took .* s of CPU time \(limit 0\.05 s"
    with pytest.raises(AssertionError, match=spinning), cpu_budget(0.05, "spinning"):
        end = time.process_time() + 0.2
        while time.process_time() < end:
            pass


@pytest.mark.parametrize(
    "options", [{"start": 0}, {"start": 1, "repeats": 0}, {"start": 1, "min_seconds": 0}]
)
def test_invalid_options_are_refused(options: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        measure_shape(lambda n: lambda: None, **options)  # type: ignore[arg-type]


def test_a_start_that_does_not_fit_the_limit_is_refused() -> None:
    with pytest.raises(ValueError, match="limit"):
        measure_shape(lambda n: lambda: None, start=10, limit=39)


def test_the_ratio_is_large_over_small() -> None:
    assert Shape(n=1, small=0.5, large=3.0).ratio == 6.0
    assert MAX_RATIO == 8.0


# --- Canaries: real code, real CPU time ---------------------------------------------------------


def _retry_from_every_position(n: int) -> Work:
    text = "1" * n
    return lambda: re.search(r"\d+x", text)  # tries every start, each one runs to the end


def _one_pass(n: int) -> Work:
    text = "1" * n
    return lambda: sum(1 for _ in re.finditer(r"\d", text))


def _python_quadratic(n: int) -> Work:
    def work() -> int:
        total = 0
        for i in range(n):
            for j in range(n):
                total += i ^ j
        return total

    return work


def _python_linear(n: int) -> Work:
    def work() -> int:
        total = 0
        for i in range(n):
            total += i ^ 7
        return total

    return work


def test_canary_a_regex_that_retries_from_every_position_fails() -> None:
    with pytest.raises(AssertionError, match="grows faster than linear"):
        assert_linear(_retry_from_every_position, start=1_000)


def test_canary_a_linear_regex_passes() -> None:
    assert_linear(_one_pass, start=10_000)


def test_canary_catastrophic_backtracking_fails_and_cannot_hang() -> None:
    # (a+)+$ doubles its work with each extra "a"; the limit keeps 4n at 20 characters (about a
    # million steps), so this canary is bounded. A wall clock: n=5 is far below one CPU tick.
    def prepare(n: int) -> Work:
        text = "a" * n + "!"
        return lambda: re.match(r"(a+)+$", text)

    with pytest.raises(AssertionError, match="grows faster than linear"):
        assert_linear(prepare, start=5, limit=20, clock=time.perf_counter)


def test_canary_a_quadratic_python_loop_fails() -> None:
    with pytest.raises(AssertionError, match="grows faster than linear"):
        assert_linear(_python_quadratic, start=100)


def test_canary_a_linear_python_loop_passes() -> None:
    assert_linear(_python_linear, start=10_000)


def test_every_cpu_budget_is_recorded_for_the_summary() -> None:
    """The CPU each budget used is printed at the end of the run, so CI shows the margin left."""
    with cpu_budget(7.5, "a recorded block"):
        pass
    what, spent, limit = CPU_SPENT[-1]
    assert (what, limit) == ("a recorded block", 7.5)
    assert 0 <= spent < 7.5
