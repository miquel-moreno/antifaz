"""The gate of «Comprobar ahora» (issue 53, PR 11e-4, ADR-0018): web/check_gate.py.

One check at a time per process, and a cooldown of 30 s counted from the END of the last check
(a slow check does not shorten the wait). begin(now) takes the check, or says why not with the
whole seconds to wait (busy: 1; cooldown: the rest, rounded up); end(now) always gives it back
(the route calls it in a finally). The clock is injected: these tests never sleep.
"""

import pytest

from antifaz.web.check_gate import COOLDOWN_SECONDS, CheckGate


def test_the_first_check_is_taken() -> None:
    gate = CheckGate()

    assert gate.begin(100.0) is None
    assert gate.running


def test_a_second_check_while_one_runs_is_busy_for_one_second() -> None:
    gate = CheckGate()
    gate.begin(100.0)

    assert gate.begin(100.5) == ("busy", 1)
    assert gate.begin(400.0) == ("busy", 1)  # however long it runs: still busy, never a queue


def test_the_cooldown_counts_from_the_end_of_the_check() -> None:
    gate = CheckGate()
    gate.begin(100.0)
    gate.end(104.0)  # it took 4 s

    assert gate.begin(104.0) == ("cooldown", 30)
    assert gate.begin(110.2) == ("cooldown", 24)  # 23.8 s left, rounded up
    assert gate.begin(133.5) == ("cooldown", 1)
    assert gate.begin(134.0) is None  # exactly 30 s after the end


def test_a_refused_begin_changes_nothing() -> None:
    gate = CheckGate()
    gate.begin(0.0)
    gate.end(1.0)

    for _ in range(5):
        assert gate.begin(2.0) == ("cooldown", 29)
    assert not gate.running
    assert gate.begin(31.0) is None


def test_the_cooldown_is_thirty_seconds_and_can_be_changed() -> None:
    assert COOLDOWN_SECONDS == 30.0
    gate = CheckGate(cooldown=2.0)
    gate.begin(0.0)
    gate.end(0.0)

    assert gate.begin(1.0) == ("cooldown", 1)
    assert gate.begin(2.0) is None


def test_the_injected_clock_is_what_now_reads() -> None:
    times = iter([7.0, 9.5])
    gate = CheckGate(clock=lambda: next(times))

    assert gate.now() == 7.0
    assert gate.now() == 9.5


def test_a_cooldown_never_says_zero_seconds() -> None:
    gate = CheckGate()
    gate.begin(0.0)
    gate.end(0.0)

    assert gate.begin(29.999) == ("cooldown", 1)


@pytest.mark.parametrize("cooldown", [-1.0, float("nan")])
def test_a_bad_cooldown_is_refused(cooldown: float) -> None:
    with pytest.raises(ValueError, match="cooldown"):
        CheckGate(cooldown=cooldown)
