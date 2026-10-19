"""The NER's «Prueba un texto» path (issue 53, PR 11d-a review): it never harms the proxy.

- NerPool.try_predict() takes a worker only if one is idle RIGHT NOW (NerBusyError otherwise,
  without waiting) and never touches the circuit breaker: a hang or a death in a Try neither
  counts as a failure nor resets the count. The worker is still killed and replaced.
- NerDetector.for_try() is a detector over the same predictor with no span cache (a Try never
  reads or writes the proxy's cache: no membership oracle, no eviction, no digest left behind),
  a short deadline, the pool's try_predict when it has one, and was_busy() for the answer.

Real worker processes (spawn) with the fake backend, as in test_pool.py; the facts counted are
the errors, the breaker's count and whether a request reached a worker.
"""

import threading
import time
from collections.abc import Iterator, Sequence

import pytest

from antifaz.detect.ner.backend import NerBusyError
from antifaz.detect.ner.cache import SpanCache
from antifaz.detect.ner.engine import NerDetector
from antifaz.detect.ner.pool import NerPool
from antifaz.errors import DetectorFailed
from tests.nerfakes import CARMEN, FAKE_FACTORY, NAMES, InProcess, fake_detector, when_ready

LABELS = ["person"]


@pytest.fixture
def slow_pool() -> Iterator[NerPool]:
    """One worker; FAKE_SLEEP holds it for 1.5 s (well under the pool's 20 s limit)."""
    pool = NerPool(
        FAKE_FACTORY,
        {"names": {CARMEN: "person"}, "triggers": True, "sleep_seconds": 1.5},
        workers=1,
        timeout=20.0,
        failure_threshold=1,
    )
    pool.start()
    yield pool
    pool.close()


@pytest.fixture
def hanging_pool() -> Iterator[NerPool]:
    pool = NerPool(
        FAKE_FACTORY,
        {"names": {CARMEN: "person"}, "triggers": True, "sleep_seconds": 60},
        workers=1,
        timeout=20.0,
        failure_threshold=1,
    )
    pool.start()
    yield pool
    pool.close()


def _wait_until(check: object, within: float = 30.0) -> None:
    give_up = time.monotonic() + within
    while not check():  # type: ignore[operator]
        assert time.monotonic() < give_up, "condition never became true"
        time.sleep(0.02)


def test_try_predict_answers_when_a_worker_is_idle(slow_pool: NerPool) -> None:
    text = f"Hola {CARMEN}"

    assert slow_pool.try_predict([text], LABELS, 0.5) == [[[5, 5 + len(CARMEN), "person", 0.9]]]


def test_a_proxy_request_in_flight_makes_a_try_busy_without_waiting(slow_pool: NerPool) -> None:
    done: list[object] = []
    proxy = threading.Thread(
        target=lambda: done.append(slow_pool.predict([f"FAKE_SLEEP {CARMEN}"], LABELS, 0.5))
    )
    proxy.start()
    try:
        _wait_until(lambda: slow_pool._busy)  # the proxy holds the only worker
        started = time.monotonic()
        with pytest.raises(NerBusyError):
            slow_pool.try_predict([CARMEN], LABELS, 0.5)
        assert time.monotonic() - started < 0.5  # never waited for the worker
    finally:
        proxy.join(timeout=30)
    assert len(done) == 1  # the proxy request was served


def test_a_try_that_hangs_never_counts_toward_the_breaker(hanging_pool: NerPool) -> None:
    when_ready(lambda: hanging_pool.predict(["calentar"], LABELS, 0.5))

    with pytest.raises(DetectorFailed):
        hanging_pool.try_predict(["FAKE_SLEEP"], LABELS, 0.5, deadline=time.monotonic() + 0.5)

    assert hanging_pool._failures == 0
    assert hanging_pool.status() != "circuit_open"
    # The replaced worker serves the proxy again (it loads in the background).
    assert when_ready(lambda: hanging_pool.predict([CARMEN], LABELS, 0.5)) != []


def test_the_same_hang_through_the_proxy_does_count(hanging_pool: NerPool) -> None:
    """The check above is not vacuous: threshold 1 opens the circuit on one proxy failure."""
    when_ready(lambda: hanging_pool.predict(["calentar"], LABELS, 0.5))

    with pytest.raises(DetectorFailed):
        hanging_pool.predict(["FAKE_SLEEP"], LABELS, 0.5, deadline=time.monotonic() + 0.5)

    assert hanging_pool._failures == 1
    assert hanging_pool.status() == "circuit_open"


def test_a_try_success_does_not_reset_the_breaker(slow_pool: NerPool) -> None:
    with slow_pool._lock:
        slow_pool._failures = 1  # as if the proxy had failed once (circuit not open: no time)
    assert slow_pool.try_predict([CARMEN], LABELS, 0.5)

    assert slow_pool._failures == 1


def test_try_predict_is_refused_while_the_circuit_is_open(slow_pool: NerPool) -> None:
    with slow_pool._lock:
        slow_pool._open_until = time.monotonic() + 60

    with pytest.raises(DetectorFailed):
        slow_pool.try_predict([CARMEN], LABELS, 0.5)


# --- NerDetector.for_try() --------------------------------------------------------------------


class TryAware(InProcess):
    """An in-process predictor with a try_predict that can say busy."""

    def __init__(self, *args: object, busy: bool = False) -> None:
        super().__init__(*args)  # type: ignore[arg-type]
        self.busy = busy
        self.try_calls = 0
        self.deadlines: list[float | None] = []

    def try_predict(
        self,
        texts: Sequence[str],
        labels: Sequence[str],
        threshold: float,
        deadline: float | None = None,
    ) -> object:
        self.try_calls += 1
        self.deadlines.append(deadline)
        if self.busy:
            raise NerBusyError()
        return self.backend.predict(list(texts), list(labels), threshold)


def test_for_try_never_reads_or_writes_the_cache() -> None:
    ner, predictor = fake_detector(NAMES)
    cache: SpanCache = ner._cache  # type: ignore[assignment]
    text = f"Soy {CARMEN}"
    ner.find_many([text])  # the proxy saw it: it is in the cache now
    calls, size = predictor.calls, len(cache)

    spans = ner.for_try(2.0).find_many([text])

    assert spans == ner.find_many([text])
    assert predictor.calls == calls + 1  # the model path, not a cache hit
    assert len(cache) == size


def test_for_try_uses_try_predict_and_a_short_deadline() -> None:
    from antifaz.detect.ner.fake import FakeBackend

    predictor = TryAware(FakeBackend(NAMES))
    ner = NerDetector(predictor, cache=SpanCache(10), timeout=10.0)  # type: ignore[arg-type]
    before = time.monotonic()

    try_ner = ner.for_try(2.0)
    try_ner.find_many([f"Soy {CARMEN}"])

    assert predictor.try_calls == 1 and predictor.calls == 0
    (deadline,) = predictor.deadlines
    assert deadline is not None and deadline <= before + 2.0 + 1.0
    assert not try_ner.was_busy()


def test_for_try_reports_busy_and_blocks() -> None:
    from antifaz.detect.ner.fake import FakeBackend

    predictor = TryAware(FakeBackend(NAMES), busy=True)
    try_ner = NerDetector(predictor).for_try(2.0)  # type: ignore[arg-type]

    with pytest.raises(NerBusyError):
        try_ner.find_many([f"Soy {CARMEN}"])
    assert try_ner.was_busy()


def test_each_for_try_has_its_own_busy_flag() -> None:
    from antifaz.detect.ner.fake import FakeBackend

    predictor = TryAware(FakeBackend(NAMES), busy=True)
    ner = NerDetector(predictor)  # type: ignore[arg-type]
    first = ner.for_try(2.0)
    with pytest.raises(NerBusyError):
        first.find_many([CARMEN])

    assert first.was_busy() and not ner.for_try(2.0).was_busy()


def test_a_predictor_without_try_predict_is_called_as_usual() -> None:
    """In-process predictors (tests, demos) share no workers: nothing to starve."""
    ner, predictor = fake_detector(NAMES)

    assert ner.for_try(2.0).find_many([CARMEN]) != [[]]
    assert predictor.calls == 1


@pytest.mark.parametrize("seconds", [0.0, -1.0])
def test_for_try_needs_a_positive_deadline(seconds: float) -> None:
    ner, _ = fake_detector(NAMES)

    with pytest.raises(ValueError):
        ner.for_try(seconds)
