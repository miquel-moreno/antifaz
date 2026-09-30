"""A pool of NER worker processes that can be killed (ADR-0003, ADR-0016).

`ProcessPoolExecutor` cannot kill a hung worker before Python 3.14, so the pool is our own:
`workers` processes started with `spawn` (the same on Windows and Linux), each loading the model
once through a factory imported by path. Every call has a time limit. If a worker goes past it,
dies or answers something malformed, it is killed and replaced, and the request is blocked with
DetectorFailed (invariant 7): the next request gets a fresh worker. A worker that reports a
backend error (a fixed code) is kept.

Parent and worker speak JSON over a pipe, with a size limit. The log only says that a worker was
replaced and why, never the text.
"""

import json
import logging
import multiprocessing
import queue
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from multiprocessing.process import BaseProcess

from antifaz.detect.ner.backend import split_factory
from antifaz.detect.ner.worker import Pipe
from antifaz.detect.ner.worker import main as worker_main
from antifaz.errors import DetectorFailed

logger = logging.getLogger("antifaz.ner")

_CONTEXT = multiprocessing.get_context("spawn")
MAX_REPLY_BYTES = 32 * 1024 * 1024
_MAX_READY_BYTES = 1024
_JOIN_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 10.0
# Loading a real model (issue 6b) takes seconds; a worker that never gets ready is replaced.
DEFAULT_LOAD_TIMEOUT_SECONDS = 300.0


class NerUnavailableError(Exception):
    """The NER workers could not load the backend at startup. Fixed message."""

    def __init__(self) -> None:
        super().__init__("the NER workers could not load the model")


class _LostError(Exception):
    """The worker hung, died or broke the protocol: it must be replaced."""


@dataclass(slots=True)
class _Worker:
    process: BaseProcess
    conn: Pipe
    ready: bool = False


class NerPool:
    """Worker processes running a NER backend, with a time limit per call."""

    def __init__(
        self,
        factory: str,
        options: Mapping[str, object] | None = None,
        *,
        workers: int = 1,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        load_timeout: float = DEFAULT_LOAD_TIMEOUT_SECONDS,
    ) -> None:
        split_factory(factory)  # a malformed path fails here, not in a child process
        if workers < 1 or timeout <= 0 or load_timeout <= 0:
            raise ValueError("the NER pool needs at least one worker and positive time limits")
        self._factory = factory
        self._options = json.dumps(dict(options or {}))
        self._size = workers
        self._timeout = timeout
        self._load_timeout = load_timeout
        self._idle: queue.Queue[_Worker] = queue.Queue()
        self._workers: list[_Worker] = []
        self._lock = threading.Lock()
        self._started = False
        self._closed = False

    # --- life cycle ---------------------------------------------------------------------------

    def start(self) -> None:
        """Start the workers and wait until every one has loaded the backend."""
        with self._lock:
            if self._started or self._closed:
                return
            workers = [self._spawn() for _ in range(self._size)]
            try:
                for worker in workers:
                    self._wait_ready(worker)
            except _LostError:
                failed = True
            else:
                failed = False
            if failed:
                for worker in workers:
                    self._stop(worker)
                raise NerUnavailableError()
            for worker in workers:
                self._idle.put(worker)
            self._started = True

    def close(self) -> None:
        """Stop every worker. Calls after this one are blocked."""
        with self._lock:
            self._closed = True
            for worker in list(self._workers):
                self._stop(worker)

    def worker_pids(self) -> tuple[int | None, ...]:
        """Process ids of the current workers (for tests and diagnostics)."""
        with self._lock:
            return tuple(worker.process.pid for worker in self._workers)

    # --- workers ------------------------------------------------------------------------------

    def _spawn(self) -> _Worker:
        parent, child = _CONTEXT.Pipe(duplex=True)
        process = _CONTEXT.Process(
            target=worker_main,
            args=(child, self._factory, self._options),
            name="antifaz-ner",
            daemon=True,
        )
        process.start()
        child.close()  # only the worker keeps its end: its death closes the pipe
        worker = _Worker(process, parent)
        self._workers.append(worker)
        return worker

    def _stop(self, worker: _Worker) -> None:
        """Kill a worker (once: a worker already stopped by close() is skipped)."""
        if worker not in self._workers:
            return
        self._workers.remove(worker)
        worker.conn.close()
        worker.process.kill()
        worker.process.join(_JOIN_SECONDS)
        if worker.process.exitcode is not None:
            worker.process.close()

    def _replace(self, worker: _Worker) -> _Worker | None:
        with self._lock:
            self._stop(worker)
            if self._closed:
                return None
            return self._spawn()  # it loads in the background; the next call waits for it

    def _wait_ready(self, worker: _Worker) -> None:
        try:
            if not worker.conn.poll(self._load_timeout):
                raise _LostError()
            reply = json.loads(worker.conn.recv_bytes(_MAX_READY_BYTES))
        except (EOFError, OSError, ValueError):
            raise _LostError() from None
        if reply != {"status": "ready"}:
            raise _LostError()
        worker.ready = True

    # --- calls --------------------------------------------------------------------------------

    def _call(
        self, worker: _Worker, texts: Sequence[str], labels: Sequence[str], threshold: float
    ) -> list[object] | None:
        """The results, None for a backend error (worker kept), or _LostError (replace it)."""
        request: dict[str, object] = {
            "op": "predict",
            "texts": list(texts),
            "labels": list(labels),
            "threshold": threshold,
        }
        try:
            if not worker.ready:
                self._wait_ready(worker)
            worker.conn.send_bytes(json.dumps(request).encode())
            if not worker.conn.poll(self._timeout):
                logger.warning("NER worker went past the time limit: replaced")
                raise _LostError()
            reply = json.loads(worker.conn.recv_bytes(MAX_REPLY_BYTES))
        except (EOFError, OSError, ValueError, RecursionError):
            logger.warning("NER worker died or broke the protocol: replaced")
            raise _LostError() from None
        if reply == {"status": "error", "code": "predict_failed"}:
            return None
        ok = isinstance(reply, dict) and reply.get("status") == "ok"
        results = reply.get("results") if ok else None
        if not isinstance(results, list) or len(results) != len(texts):
            logger.warning("NER worker gave a malformed answer: replaced")
            raise _LostError()
        return results

    def predict(self, texts: Sequence[str], labels: Sequence[str], threshold: float) -> object:
        """One list of raw entities per text (checked by the engine), or DetectorFailed."""
        if not self._started or self._closed:
            raise DetectorFailed()
        try:
            worker = self._idle.get(timeout=self._timeout)
        except queue.Empty:
            worker = None
        if worker is None:  # every worker stayed busy for the whole time limit
            raise DetectorFailed()
        results: list[object] | None = None
        lost = True
        try:
            results = self._call(worker, texts, labels, threshold)
            lost = False
        except _LostError:
            pass
        finally:
            back = self._replace(worker) if lost else worker
            if back is not None and not self._closed:
                self._idle.put(back)
        if results is None:  # raised here, outside any except block: nothing is chained
            raise DetectorFailed()
        return results
