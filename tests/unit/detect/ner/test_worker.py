"""The worker loop, run in a thread of the test process (the pool tests run it in real processes).

The worker speaks JSON only, never pickle, and answers errors with fixed codes: never the text
of an exception, which may hold the data it was reading.
"""

import json
import multiprocessing
import threading
from collections.abc import Iterator
from multiprocessing.connection import Connection

import pytest

from antifaz.detect.ner.worker import OFFLINE_ENV, run
from tests.conftest import SENTINEL_DNI
from tests.nerfakes import CARMEN, FAKE_FACTORY


def _serve(factory: str, options: object) -> Iterator[tuple[Connection, threading.Thread]]:
    parent, child = multiprocessing.Pipe()
    thread = threading.Thread(target=run, args=(child, factory, json.dumps(options)), daemon=True)
    thread.start()
    yield parent, thread
    parent.close()
    thread.join(5)


@pytest.fixture
def worker() -> Iterator[Connection]:
    for parent, _ in _serve(FAKE_FACTORY, {"names": {CARMEN: "person"}, "triggers": True}):
        assert json.loads(parent.recv_bytes()) == {"status": "ready"}
        yield parent


def _ask(conn: Connection, request: object) -> object:
    conn.send_bytes(json.dumps(request).encode())
    return json.loads(conn.recv_bytes())


def test_it_answers_predictions_in_json(worker: Connection) -> None:
    request = {"op": "predict", "texts": [CARMEN], "labels": ["person"], "threshold": 0.5}
    assert _ask(worker, request) == {
        "status": "ok",
        "results": [[[0, len(CARMEN), "person", 0.9]]],
    }


def test_a_backend_error_is_a_fixed_code_without_the_text(worker: Connection) -> None:
    request = {
        "op": "predict",
        "texts": [f"FAKE_RAISE {SENTINEL_DNI}"],
        "labels": [],
        "threshold": 1,
    }
    answer = _ask(worker, request)
    assert answer == {"status": "error", "code": "predict_failed"}
    assert SENTINEL_DNI not in json.dumps(answer)


@pytest.mark.parametrize("raw", [b"{", b"[]", b'{"op": "predict"}', b"\xff"])
def test_a_malformed_request_is_a_fixed_code(worker: Connection, raw: bytes) -> None:
    worker.send_bytes(raw)
    assert json.loads(worker.recv_bytes()) == {"status": "error", "code": "predict_failed"}


def test_stop_ends_the_loop() -> None:
    for parent, thread in _serve(FAKE_FACTORY, {"names": {}}):
        assert json.loads(parent.recv_bytes()) == {"status": "ready"}
        parent.send_bytes(b'{"op": "stop"}')
        thread.join(5)
        assert not thread.is_alive()


def test_a_closed_pipe_ends_the_loop() -> None:
    for parent, thread in _serve(FAKE_FACTORY, {"names": {}}):
        assert json.loads(parent.recv_bytes()) == {"status": "ready"}
        parent.close()
        thread.join(5)
        assert not thread.is_alive()


@pytest.mark.parametrize(
    ("factory", "options"),
    [(FAKE_FACTORY, {"unknown": 1}), (FAKE_FACTORY, []), ("no_such_module_xyz:create", {})],
)
def test_a_backend_that_cannot_load_answers_a_fixed_code(factory: str, options: object) -> None:
    for parent, thread in _serve(factory, options):
        assert json.loads(parent.recv_bytes()) == {"status": "error", "code": "load_failed"}
        thread.join(5)
        assert not thread.is_alive()


def test_workers_run_offline() -> None:
    assert OFFLINE_ENV["HF_HUB_OFFLINE"] == "1"
    assert OFFLINE_ENV["TRANSFORMERS_OFFLINE"] == "1"
