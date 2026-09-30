"""The NER process pool (ADR-0016): real worker processes (spawn) running the fake backend.

A hung worker is killed at the time limit and replaced, a dead one too, and both block the
request (invariant 7). Nothing the worker sees is written to the logs or to stdout/stderr.
"""

import logging
import threading
import time
from collections.abc import Iterator

import pytest

from antifaz.detect.ner.engine import NerDetector
from antifaz.detect.ner.pool import NerPool, NerUnavailableError
from antifaz.errors import DetectorFailed
from tests.conftest import SENTINEL_DNI
from tests.nerfakes import CARMEN, FAKE_FACTORY

OPTIONS = {"names": {CARMEN: "person"}, "triggers": True, "sleep_seconds": 60}
TIMEOUT = 2.0


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


def test_a_hung_worker_is_killed_at_the_time_limit_and_replaced(pool: NerPool) -> None:
    before = pool.worker_pids()
    started = time.monotonic()
    with pytest.raises(DetectorFailed):
        pool.predict([f"FAKE_SLEEP {CARMEN}"], ["person"], 0.5)
    assert time.monotonic() - started < TIMEOUT + 10  # never the 60 s the backend sleeps
    assert pool.predict([CARMEN], ["person"], 0.5) == [_person(CARMEN)]
    assert pool.worker_pids() != before


def test_a_worker_that_dies_blocks_the_request_and_is_replaced(pool: NerPool) -> None:
    before = pool.worker_pids()
    with pytest.raises(DetectorFailed):
        pool.predict(["FAKE_CRASH"], ["person"], 0.5)
    assert pool.predict([CARMEN], ["person"], 0.5) == [_person(CARMEN)]
    assert pool.worker_pids() != before


def test_a_backend_error_blocks_without_its_text_and_keeps_the_worker(pool: NerPool) -> None:
    pool.predict(["calentar"], ["person"], 0.5)  # make sure the worker is loaded
    before = pool.worker_pids()
    with pytest.raises(DetectorFailed) as error:
        pool.predict([f"FAKE_RAISE {SENTINEL_DNI}"], ["person"], 0.5)
    assert SENTINEL_DNI not in str(error.value)
    assert error.value.__cause__ is None
    assert pool.worker_pids() == before


def test_malformed_output_through_the_engine_blocks(pool: NerPool) -> None:
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
            with pytest.raises(DetectorFailed):
                quiet.predict([f"{trigger} {SENTINEL_DNI} {CARMEN}"], ["person"], 0.5)
    finally:
        quiet.close()
    captured = capfd.readouterr()
    for value in (SENTINEL_DNI, CARMEN):
        assert value not in captured.out
        assert value not in captured.err
        assert value not in caplog.text
