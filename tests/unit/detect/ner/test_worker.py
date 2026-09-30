"""The worker loop, run in a thread of the test process (the pool tests run it in real processes).

The worker speaks JSON only, never pickle, and answers errors with fixed codes: never the text
of an exception, which may hold the data it was reading.
"""

import hashlib
import json
import multiprocessing
import threading
from collections.abc import Iterator
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from antifaz.detect.ner.manifest import load_manifest
from antifaz.detect.ner.worker import OFFLINE_ENV, run
from tests.conftest import SENTINEL_DNI
from tests.nerfakes import CARMEN, FAKE_FACTORY


def _serve(
    factory: str, options: object, verify: object = None
) -> Iterator[tuple[Connection, threading.Thread]]:
    parent, child = multiprocessing.Pipe()
    args = (child, factory, json.dumps(options), json.dumps(verify or {}))
    thread = threading.Thread(target=run, args=args, daemon=True)
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


def _model(tmp_path: Path) -> dict[str, str]:
    """A model directory and its manifest; the worker checks them again before loading."""
    directory = tmp_path / "model"
    directory.mkdir()
    (directory / "w.bin").write_bytes(b"synthetic weights")
    entry = {"size": 17, "sha256": hashlib.sha256(b"synthetic weights").hexdigest()}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"format": 1, "model": "m", "revision": None, "files": {"w.bin": entry}}),
        encoding="utf-8",
    )
    digest = load_manifest(manifest).digest
    return {"model_dir": str(directory), "manifest": str(manifest), "digest": digest}


def test_a_worker_checks_the_model_again_before_loading(tmp_path: Path) -> None:
    verify = _model(tmp_path)
    for parent, _ in _serve(FAKE_FACTORY, {"names": {}}, verify):
        assert json.loads(parent.recv_bytes()) == {"status": "ready"}


@pytest.mark.parametrize("tamper", ["file", "digest", "manifest"])
def test_a_worker_refuses_a_model_changed_after_startup(tmp_path: Path, tamper: str) -> None:
    verify = _model(tmp_path)
    if tamper == "file":
        Path(verify["model_dir"], "w.bin").write_bytes(b"synthetic weightz")
    elif tamper == "digest":
        verify["digest"] = "0" * 64
    else:
        Path(verify["manifest"]).write_text("{}", encoding="utf-8")
    for parent, thread in _serve(FAKE_FACTORY, {"names": {}}, verify):
        assert json.loads(parent.recv_bytes()) == {"status": "error", "code": "load_failed"}
        thread.join(5)
