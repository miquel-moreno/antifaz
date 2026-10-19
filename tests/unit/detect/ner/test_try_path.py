"""The NER's «Prueba un texto» path (issue 53, PR 11d-a reviews): the proxy goes first.

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
    slow_pool.predict(["calentar"], LABELS, 0.5)  # its first inference is the proxy's

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
    slow_pool.predict(["calentar"], LABELS, 0.5)
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


# --- second review: the proxy goes first, work sized to the deadline, a Try-only cooldown ------


class Counting:
    """The pool, counting the calls a Try detector makes (one per batch)."""

    def __init__(self, pool: NerPool) -> None:
        self.pool = pool
        self.try_calls = 0

    def start(self) -> None:  # pragma: no cover - the pool is started by its fixture
        self.pool.start()

    def close(self) -> None:  # pragma: no cover
        self.pool.close()

    @property
    def timeout(self) -> float:
        return self.pool.timeout

    def predict(self, *args: object, **kwargs: object) -> object:
        return self.pool.predict(*args, **kwargs)  # type: ignore[arg-type]

    def try_predict(self, *args: object, **kwargs: object) -> object:
        self.try_calls += 1
        return self.pool.try_predict(*args, **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def batch_pool() -> Iterator[NerPool]:
    """One worker; every FAKE_SLEEP text holds it for 0.6 s."""
    pool = NerPool(
        FAKE_FACTORY,
        {"names": {CARMEN: "person"}, "triggers": True, "sleep_seconds": 0.6},
        workers=1,
        timeout=20.0,
    )
    pool.start()
    yield pool
    pool.close()


def test_a_proxy_request_arriving_mid_try_waits_one_try_batch_at_most(
    batch_pool: NerPool,
) -> None:
    counting = Counting(batch_pool)
    # window 2 / overlap 1 over six FAKE_SLEEP tokens: 5 windows, one per Try batch (0.6 s each)
    base = NerDetector(counting, window=2, overlap=1)  # type: ignore[arg-type]
    try_ner = base.for_try(20.0, max_chunks=10, batch_chunks=1)
    outcome: list[BaseException] = []

    def run_try() -> None:
        try:
            try_ner.find_many(["FAKE_SLEEP " * 6])
        except BaseException as error:
            outcome.append(error)

    batch_pool.predict(["calentar"], LABELS, 0.5)  # the worker's first inference: the proxy's
    trying = threading.Thread(target=run_try)
    trying.start()
    try:
        _wait_until(lambda: batch_pool._busy)  # the Try's first batch holds the worker
        started = time.monotonic()
        served = batch_pool.predict([CARMEN], LABELS, 0.5)
        waited = time.monotonic() - started
    finally:
        trying.join(timeout=30)

    assert served == [[[0, len(CARMEN), "person", 0.9]]]
    assert [type(e) for e in outcome] == [NerBusyError]  # the Try was cut short
    assert try_ner.was_busy()
    assert counting.try_calls <= 2  # cut after the batch the proxy request arrived during
    # Very loose on purpose (no fixed timing limits): one 0.6 s batch plus generous overhead.
    assert waited < 0.6 * 2 + 2.0


def test_a_try_is_busy_while_a_proxy_request_waits(batch_pool: NerPool) -> None:
    with batch_pool._lock:
        batch_pool._waiting = 1  # a proxy request waiting for a worker (the worker is idle)
    try:
        started = time.monotonic()
        with pytest.raises(NerBusyError):
            batch_pool.try_predict([CARMEN], LABELS, 0.5)
        assert time.monotonic() - started < 0.5
    finally:
        with batch_pool._lock:
            batch_pool._waiting = 0
    assert batch_pool._idle.qsize() == 1  # the worker stayed for the proxy


def test_proxy_waiters_are_counted_while_they_wait(batch_pool: NerPool) -> None:
    holders = [
        threading.Thread(target=lambda: batch_pool.predict(["FAKE_SLEEP"], LABELS, 0.5))
        for _ in range(2)
    ]
    for thread in holders:
        thread.start()
    try:
        _wait_until(lambda: batch_pool._waiting == 1)  # one holds the worker, one waits
        with pytest.raises(NerBusyError):
            batch_pool.try_predict([CARMEN], LABELS, 0.5)
    finally:
        for thread in holders:
            thread.join(timeout=30)
    assert batch_pool._waiting == 0


class _NeverReady:
    def __init__(self, conn: object) -> None:
        self._conn = conn

    def poll(self, timeout: float = 0.0) -> bool:
        return False

    def __getattr__(self, name: str) -> object:
        return getattr(self._conn, name)


def test_a_try_never_waits_for_a_loading_worker(batch_pool: NerPool) -> None:
    (worker,) = list(batch_pool._idle.queue)
    conn = worker.conn
    worker.ready = False
    worker.conn = _NeverReady(conn)  # type: ignore[assignment]
    try:
        started = time.monotonic()
        with pytest.raises(NerBusyError):
            batch_pool.try_predict([CARMEN], LABELS, 0.5)
        assert time.monotonic() - started < 0.5
        assert batch_pool._idle.qsize() == 1  # put back, untouched
        assert batch_pool._failures == 0
    finally:
        worker.conn = conn
        worker.ready = True
    assert batch_pool.predict([CARMEN], LABELS, 0.5)


def test_a_try_that_breaks_a_worker_starts_a_try_only_cooldown(hanging_pool: NerPool) -> None:
    when_ready(lambda: hanging_pool.predict(["calentar"], LABELS, 0.5))
    with pytest.raises(DetectorFailed):
        hanging_pool.try_predict(["FAKE_SLEEP"], LABELS, 0.5, deadline=time.monotonic() + 0.5)
    replaced = hanging_pool.worker_pids()
    when_ready(lambda: hanging_pool.predict(["calentar"], LABELS, 0.5))  # the new one is up

    with pytest.raises(NerBusyError):  # no second Try within the cooldown
        hanging_pool.try_predict(["FAKE_SLEEP"], LABELS, 0.5, deadline=time.monotonic() + 0.5)

    assert hanging_pool.worker_pids() == replaced  # no second respawn
    assert hanging_pool._failures == 0  # the proxy's breaker is untouched
    assert hanging_pool.predict([CARMEN], LABELS, 0.5)  # the proxy is served meanwhile


def test_the_cooldown_ends() -> None:
    pool = NerPool(FAKE_FACTORY, {"names": {CARMEN: "person"}}, try_cooldown_seconds=0.2)
    pool.start()
    try:
        pool.predict(["calentar"], LABELS, 0.5)
        with pool._lock:
            pool._try_cooldown_until = time.monotonic() + 0.2
        with pytest.raises(NerBusyError):
            pool.try_predict([CARMEN], LABELS, 0.5)
        _wait_until(lambda: time.monotonic() > pool._try_cooldown_until)
        assert pool.try_predict([CARMEN], LABELS, 0.5)
    finally:
        pool.close()


def test_the_default_cooldown_is_a_minute() -> None:
    from antifaz.detect.ner.pool import DEFAULT_TRY_COOLDOWN_SECONDS

    assert DEFAULT_TRY_COOLDOWN_SECONDS == 60.0


def test_for_try_refuses_more_chunks_than_its_budget_without_calling_the_model() -> None:
    from antifaz.detect.ner.fake import FakeBackend

    predictor = TryAware(FakeBackend(NAMES))
    base = NerDetector(predictor, window=2, overlap=1)  # type: ignore[arg-type]
    try_ner = base.for_try(5.0, max_chunks=3, batch_chunks=1)

    with pytest.raises(DetectorFailed):
        try_ner.find_many(["uno dos tres cuatro cinco seis"])  # 5 windows > 3

    assert try_ner.was_too_long() and not try_ner.was_busy()
    assert predictor.try_calls == 0 and predictor.calls == 0  # no worker touched
    small = base.for_try(5.0, max_chunks=3, batch_chunks=1)
    assert small.find_many([f"{CARMEN}"]) and not small.was_too_long()


def test_for_try_sends_one_batch_per_call() -> None:
    from antifaz.detect.ner.fake import FakeBackend

    predictor = TryAware(FakeBackend(NAMES))
    base = NerDetector(predictor, window=2, overlap=1)  # type: ignore[arg-type]

    base.for_try(5.0, max_chunks=10, batch_chunks=2).find_many(["a b c d e f"])

    assert predictor.try_calls == 3  # 5 windows, 2 per batch


# --- last review: a failed load is replaced at once; a fresh worker serves the proxy first ----


class _FailedLoad:
    """A worker connection whose ready message says the model did not load."""

    def __init__(self, conn: object) -> None:
        self._conn = conn

    def poll(self, timeout: float = 0.0) -> bool:
        return True

    def recv_bytes(self, maxlength: int | None = None) -> bytes:
        return b'{"status": "load_failed"}'

    def __getattr__(self, name: str) -> object:
        return getattr(self._conn, name)


def test_a_failed_load_seen_by_a_try_is_replaced_at_once(batch_pool: NerPool) -> None:
    batch_pool.predict(["calentar"], LABELS, 0.5)
    (worker,) = list(batch_pool._idle.queue)
    before = batch_pool.worker_pids()
    worker.ready = False
    worker.conn = _FailedLoad(worker.conn)  # type: ignore[assignment]

    with pytest.raises(NerBusyError):
        batch_pool.try_predict([CARMEN], LABELS, 0.5)

    assert batch_pool.worker_pids() != before  # replaced now, not left for the proxy
    assert worker not in list(batch_pool._idle.queue)
    assert batch_pool._failures == 0  # the proxy's breaker is untouched
    assert time.monotonic() < batch_pool._try_cooldown_until  # a Try-only cooldown started
    # The next proxy request waits for the fresh worker to load and is served, not blocked.
    assert batch_pool.predict([CARMEN], LABELS, 0.5) == [[[0, len(CARMEN), "person", 0.9]]]


def test_a_try_never_takes_a_worker_that_has_not_served_the_proxy_yet() -> None:
    pool = NerPool(FAKE_FACTORY, {"names": {CARMEN: "person"}}, workers=1, timeout=20.0)
    pool.start()
    try:
        with pytest.raises(NerBusyError):  # loaded, but its first inference would be the Try's
            pool.try_predict([CARMEN], LABELS, 0.5)
        assert pool._idle.qsize() == 1

        pool.predict(["calentar"], LABELS, 0.5)

        assert pool.try_predict([CARMEN], LABELS, 0.5) == [[[0, len(CARMEN), "person", 0.9]]]
    finally:
        pool.close()
