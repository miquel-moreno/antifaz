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
from pathlib import Path
from typing import Protocol

from antifaz.detect.ner.backend import NerBackend, load_factory
from antifaz.detect.ner.manifest import ModelMismatchError, load_manifest, verify_model_dir


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


def main(conn: Pipe, factory: str, options: str, verify: str) -> None:  # pragma: no cover
    """Entry point of a worker process (the pool tests run it for real)."""
    _silence()
    os.environ.update(OFFLINE_ENV)
    run(conn, factory, options, verify)


def _verified(verify: str) -> bool:
    """With {"model_dir", "manifest", "digest"}: the model is still exactly the manifest's."""
    try:
        check = json.loads(verify)
        if not check:
            return True
        manifest = load_manifest(Path(check["manifest"]))
        if manifest.digest != check["digest"]:
            return False
        verify_model_dir(Path(check["model_dir"]), manifest)
    except (ModelMismatchError, OSError, ValueError, KeyError, TypeError):
        return False
    return True


def _load(factory: str, options: str, verify: str) -> NerBackend | None:
    if not _verified(verify):
        return None
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


def run(conn: Pipe, factory: str, options: str, verify: str = "{}") -> None:
    """Check the model again (at every worker start: TOCTOU), load the backend, say "ready"
    and answer requests until "stop" or the pipe closes."""
    backend = _load(factory, options, verify)
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
