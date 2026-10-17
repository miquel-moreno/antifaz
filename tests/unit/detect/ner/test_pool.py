"""The NER process pool (ADR-0016): real worker processes (spawn) running the fake backend.

A hung worker is killed at the time limit and replaced, a dead one too, and both block the
request (invariant 7). Nothing the worker sees is written to the logs or to stdout/stderr.

These tests count facts, not seconds: the error raised, what the pool logged, which worker
processes are alive, whether a request reached a worker. A replaced worker loads in the
background, so a test waits until it is ready (`when_ready`) before asking it to serve. The only
time limits left are far from what the fake would take (its FAKE_SLEEP lasts 60 s).
"""

import logging
import multiprocessing
import threading
import time
from collections.abc import Callable, Iterator
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any

import pytest

from antifaz.detect.ner.engine import NerDetector
from antifaz.detect.ner.pool import NerPool, NerUnavailableError
from antifaz.errors import DetectorFailed
from tests.conftest import SENTINEL_DNI
from tests.nerfakes import CARMEN, FAKE_FACTORY, READY_WITHIN_SECONDS, when_ready

OPTIONS = {"names": {CARMEN: "person"}, "triggers": True, "sleep_seconds": 60}
TIMEOUT = 2.0
FAR_BELOW_THE_HANG = 30.0  # seconds; FAKE_SLEEP would hold a worker for 60
# How long a request may be held past its deadline: killing and replacing a worker took under
# 0.2 s on a machine with 47 CPU burners; a slow kill or a late watchdog takes seconds more.
OVERSHOOT = 3.0
HUNG = "went past the time limit"
DIED = "died or broke the protocol"


@pytest.fixture(scope="module")
def pool() -> Iterator[NerPool]:
    ner_pool = NerPool(FAKE_FACTORY, OPTIONS, workers=1, timeout=TIMEOUT)
    ner_pool.start()
    yield ner_pool
    ner_pool.close()


def _person(text: str) -> list[list[object]]:
    start = text.index(CARMEN)
    return [[start, start + len(CARMEN), "person", 0.9]]


def test_a_worker_answers_with_the_entities(pool: NerPool) -> None:
    text = f"Hola {CARMEN}"
    assert pool.predict([text, "nada"], ["person"], 0.5) == [_person(text), []]


def _workers(pool: NerPool) -> set[BaseProcess]:
    """The pool's worker processes, as process objects (never compared by pid, which the
    system can reuse); `active_children` also reaps the finished ones."""
    pids = set(pool.worker_pids())
    return {child for child in multiprocessing.active_children() if child.pid in pids}


def _alive(processes: set[BaseProcess]) -> set[BaseProcess]:
    return processes & set(multiprocessing.active_children())


def _ask(pool: NerPool, text: str) -> Callable[[], object]:
    return lambda: pool.predict([text], ["person"], 0.5)


def test_a_hung_worker_is_killed_at_the_time_limit_and_replaced(
    pool: NerPool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="antifaz.ner")
    when_ready(_ask(pool, "calentar"))  # the hang must reach a loaded worker
    before = _workers(pool)
    hung = caplog.text.count(HUNG)
    started = time.monotonic()
    with pytest.raises(DetectorFailed):
        pool.predict([f"FAKE_SLEEP {CARMEN}"], ["person"], 0.5)
    assert time.monotonic() - started < TIMEOUT + OVERSHOOT  # released at its deadline
    assert caplog.text.count(HUNG) == hung + 1  # the deadline, not another failure
    assert not _alive(before)  # the hung process is gone...
    assert _workers(pool) and before.isdisjoint(_workers(pool))  # ...and replaced
    assert when_ready(_ask(pool, CARMEN)) == [_person(CARMEN)]  # the replacement serves


def test_a_worker_that_dies_blocks_the_request_and_is_replaced(
    pool: NerPool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="antifaz.ner")
    when_ready(_ask(pool, "calentar"))  # the crash must reach a loaded worker
    before = _workers(pool)
    died = caplog.text.count(DIED)
    with pytest.raises(DetectorFailed):
        pool.predict(["FAKE_CRASH"], ["person"], 0.5)
    assert caplog.text.count(DIED) == died + 1
    assert not _alive(before)
    assert _workers(pool) and before.isdisjoint(_workers(pool))
    assert when_ready(_ask(pool, CARMEN)) == [_person(CARMEN)]


def test_a_backend_error_blocks_without_its_text_and_keeps_the_worker(pool: NerPool) -> None:
    when_ready(_ask(pool, "calentar"))  # make sure the worker is loaded
    before = _workers(pool)
    with pytest.raises(DetectorFailed) as error:
        pool.predict([f"FAKE_RAISE {SENTINEL_DNI}"], ["person"], 0.5)
    assert SENTINEL_DNI not in str(error.value)
    assert error.value.__cause__ is None
    assert before and _workers(pool) == before  # the same process objects


def test_malformed_output_through_the_engine_blocks(pool: NerPool) -> None:
    when_ready(_ask(pool, "calentar"))  # a worker still loading would block for another reason
    with pytest.raises(DetectorFailed):
        NerDetector(pool).find_many(["FAKE_MALFORMED"])


def test_a_pool_that_cannot_load_its_backend_refuses_to_start() -> None:
    broken = NerPool(FAKE_FACTORY, {"no_such_option": 1}, workers=1, timeout=TIMEOUT)
    with pytest.raises(NerUnavailableError):
        broken.start()
    with pytest.raises(DetectorFailed):
        broken.predict(["hola"], ["person"], 0.5)


def test_a_malformed_factory_path_is_refused_before_spawning() -> None:
    with pytest.raises(ValueError, match="factory"):
        NerPool("not a path", {})


@pytest.mark.parametrize("options", [{"workers": 0}, {"timeout": 0}, {"load_timeout": -1}])
def test_invalid_pool_settings_are_refused(options: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        NerPool(FAKE_FACTORY, {}, **options)  # type: ignore[arg-type]


def test_a_closed_or_unstarted_pool_blocks() -> None:
    idle = NerPool(FAKE_FACTORY, OPTIONS)
    with pytest.raises(DetectorFailed):
        idle.predict(["hola"], ["person"], 0.5)
    idle.close()
    with pytest.raises(DetectorFailed):
        idle.predict(["hola"], ["person"], 0.5)


def test_two_workers_answer_requests_from_several_threads() -> None:
    two = NerPool(FAKE_FACTORY, OPTIONS, workers=2, timeout=TIMEOUT)
    two.start()
    try:
        assert len(two.worker_pids()) == 2
        results: list[object] = []

        def ask() -> None:
            results.append(two.predict([CARMEN], ["person"], 0.5))

        threads = [threading.Thread(target=ask) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert results == [[_person(CARMEN)]] * 6
    finally:
        two.close()
    assert two.worker_pids() == ()


def test_workers_write_nothing_the_text_holds(
    caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    """Invariant 8: the worker's stdout, stderr and logging never show the text it read."""
    caplog.set_level(logging.DEBUG)
    quiet = NerPool(FAKE_FACTORY, OPTIONS, workers=1, timeout=TIMEOUT)
    quiet.start()  # started here, so the workers inherit the captured stdout and stderr
    try:
        text = f"FAKE_PRINT {SENTINEL_DNI} {CARMEN}"
        assert quiet.predict([text], ["person"], 0.5) == [_person(text)]
        for trigger in ("FAKE_RAISE", "FAKE_SLEEP", "FAKE_CRASH"):
            when_ready(_ask(quiet, "calentar"))  # each trigger reaches a loaded worker
            with pytest.raises(DetectorFailed):
                quiet.predict([f"{trigger} {SENTINEL_DNI} {CARMEN}"], ["person"], 0.5)
    finally:
        quiet.close()
    captured = capfd.readouterr()
    for value in (SENTINEL_DNI, CARMEN):
        assert value not in captured.out
        assert value not in captured.err
        assert value not in caplog.text


# --- Loading, circuit breaker and shutdown (review of 6a) --------------------------------------


def test_a_request_waits_at_most_its_time_limit_for_a_reloading_worker(tmp_path: Path) -> None:
    delay = tmp_path / "load_seconds"  # read by the fake backend when a worker loads
    options = {**OPTIONS, "load_delay_file": str(delay)}
    slow_reload = NerPool(FAKE_FACTORY, options, workers=1, timeout=0.5)
    slow_reload.start()
    try:
        delay.write_text("6", encoding="utf-8")
        with pytest.raises(DetectorFailed):
            slow_reload.predict(["FAKE_CRASH"], ["person"], 0.5)  # the new worker loads for 6 s
        began = time.monotonic()
        with pytest.raises(DetectorFailed):
            slow_reload.predict([CARMEN], ["person"], 0.5)
        assert time.monotonic() - began < 0.5 + OVERSHOOT  # its own limit, not the 6 s load
        assert slow_reload.status() == "starting"
        # Once loaded (plus starting a process, slow on a busy Windows machine) it serves.
        assert when_ready(_ask(slow_reload, CARMEN)) == [_person(CARMEN)]
        assert slow_reload.status() == "ok"
    finally:
        slow_reload.close()


def _count_calls(pool: NerPool, monkeypatch: pytest.MonkeyPatch) -> list[None]:
    """One entry per request that reached a worker (the pool's private `_call`, spied)."""
    calls: list[None] = []
    real = pool._call

    def spy(*args: Any, **kwargs: Any) -> object:
        calls.append(None)
        return real(*args, **kwargs)

    monkeypatch.setattr(pool, "_call", spy)
    return calls


def _crash(pool: NerPool, caplog: pytest.LogCaptureFixture) -> None:
    """Send FAKE_CRASH until it reaches a loaded worker (the pool logs one more death): one
    failure in a row.

    A worker still loading only blocks the request, neither a failure nor a success, so this
    never resets the count the way a warm-up answer would."""
    give_up = time.monotonic() + READY_WITHIN_SECONDS
    died = caplog.text.count(DIED)
    while True:
        with pytest.raises(DetectorFailed):
            pool.predict([f"FAKE_CRASH {SENTINEL_DNI}"], ["person"], 0.5)
        if caplog.text.count(DIED) > died:
            return
        assert time.monotonic() < give_up, "the replacement worker never got ready"


def test_repeated_failures_open_the_circuit_and_it_closes_after_the_backoff(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.WARNING)
    breaker = NerPool(
        FAKE_FACTORY, OPTIONS, workers=1, timeout=TIMEOUT, failure_threshold=2, backoff_seconds=5.0
    )
    breaker.start()
    calls = _count_calls(breaker, monkeypatch)
    try:
        for _ in range(2):
            _crash(breaker, caplog)
        assert breaker.status() == "circuit_open"
        assert caplog.text.count("NER circuit open") == 1
        reached = len(calls)
        waited: list[None] = []
        real_get = breaker._idle.get

        def get(*args: Any, **kwargs: Any) -> object:
            waited.append(None)
            return real_get(*args, **kwargs)

        monkeypatch.setattr(breaker._idle, "get", get)
        with pytest.raises(DetectorFailed):
            breaker.predict([CARMEN], ["person"], 0.5)  # blocked at once: circuit open
        assert len(calls) == reached  # no worker was asked
        assert waited == []  # nor waited for: refused before taking a worker
        monkeypatch.undo()  # the next request takes a worker again
        give_up = time.monotonic() + READY_WITHIN_SECONDS
        while breaker.status() == "circuit_open" and time.monotonic() < give_up:
            time.sleep(0.05)  # until the 5 s backoff has passed (long, so no check races it)
        assert when_ready(_ask(breaker, CARMEN)) == [_person(CARMEN)]
        assert breaker.status() == "ok"
    finally:
        breaker.close()
    assert "circuit open" in caplog.text
    assert SENTINEL_DNI not in caplog.text


def test_closing_during_a_call_blocks_that_call_without_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    busy = NerPool(FAKE_FACTORY, OPTIONS, workers=1, timeout=60.0)
    busy.start()
    calls = _count_calls(busy, monkeypatch)
    errors: list[BaseException] = []

    def hang() -> None:
        try:
            busy.predict(["FAKE_SLEEP"], ["person"], 0.5)
        except BaseException as error:  # the test inspects what was raised
            errors.append(error)

    thread = threading.Thread(target=hang)
    thread.start()
    give_up = time.monotonic() + READY_WITHIN_SECONDS
    while not calls and time.monotonic() < give_up:
        time.sleep(0.01)  # until the request holds the worker
    assert calls, "the request never reached the worker"
    busy.close()
    thread.join(FAR_BELOW_THE_HANG)
    assert not thread.is_alive()  # it waited neither the 60 s sleep nor the 60 s limit
    assert len(errors) == 1 and isinstance(errors[0], DetectorFailed)
    assert busy.worker_pids() == ()
    assert busy.status() == "closed"


def test_the_pool_exposes_its_time_limit() -> None:
    assert NerPool(FAKE_FACTORY, OPTIONS, timeout=3.0).timeout == 3.0
