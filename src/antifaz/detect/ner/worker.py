"""The NER worker: runs in its own process, loads the model once and answers predictions.

It speaks JSON over a pipe (never pickle: a compromised model cannot run code in the gateway
through its answers). Errors are fixed codes, never the text of an exception (it may hold the
data being read). It writes nothing: stdin, stdout and stderr go to the null device, logging
and warnings are off. It never touches the network: the Hugging Face libraries are set offline
before the backend is imported.
"""

import json
import logging
import os
import sys
import warnings
from typing import Protocol

from antifaz.detect.ner.backend import NerBackend, load_factory


class Pipe(Protocol):
    """The end of a multiprocessing pipe (Connection, or PipeConnection on Windows)."""

    def send_bytes(self, buf: bytes) -> None: ...

    def recv_bytes(self, maxlength: int | None = None) -> bytes: ...

    def poll(self, timeout: float | None = 0.0) -> bool: ...

    def close(self) -> None: ...


OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
}
_READY = json.dumps({"status": "ready"}).encode()
_LOAD_FAILED = json.dumps({"status": "error", "code": "load_failed"}).encode()
_PREDICT_FAILED = json.dumps({"status": "error", "code": "predict_failed"}).encode()


def _silence() -> None:  # pragma: no cover - only in the worker process
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - process-long
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("ignore")


def main(conn: Pipe, factory: str, options: str) -> None:  # pragma: no cover - in child
    """Entry point of a worker process (the pool tests run it for real)."""
    _silence()
    os.environ.update(OFFLINE_ENV)
    run(conn, factory, options)


def _load(factory: str, options: str) -> NerBackend | None:
    try:
        return load_factory(factory)(**json.loads(options))
    except Exception:  # any failure is one fixed code; the error may quote the model files
        return None


def _answer(backend: NerBackend, raw: bytes) -> bytes | None:
    """The reply to one request, or None for "stop"."""
    try:
        request = json.loads(raw)
        if request.get("op") == "stop":
            return None
        results = backend.predict(
            [str(text) for text in request["texts"]],
            [str(label) for label in request["labels"]],
            float(request["threshold"]),
        )
        return json.dumps({"status": "ok", "results": results}).encode()
    except Exception:  # a fixed code: the error may hold the text being read
        return _PREDICT_FAILED


def run(conn: Pipe, factory: str, options: str) -> None:
    """Load the backend, say "ready" and answer requests until "stop" or the pipe closes."""
    backend = _load(factory, options)
    if backend is None:
        conn.send_bytes(_LOAD_FAILED)
        return
    conn.send_bytes(_READY)
    while True:
        try:
            raw = conn.recv_bytes()
        except (EOFError, OSError):
            return
        reply = _answer(backend, raw)
        if reply is None:
            return
        conn.send_bytes(reply)
