"""A pool of NER worker processes that can be killed (ADR-0003, ADR-0016).

`ProcessPoolExecutor` cannot kill a hung worker before Python 3.14, so the pool is our own:
`workers` processes started with `spawn` (the same on Windows and Linux), each loading the model
once through a factory imported by path.

Every request has ONE deadline (`timeout`, or an earlier one from the caller) that covers
waiting for a free worker, waiting for a worker that is still loading, sending, and receiving
the WHOLE answer: a watchdog kills the worker at the deadline, so even an answer that stops
half-way cannot hold the request. A worker that goes past the deadline, dies or answers
something malformed is killed and replaced, and the request is blocked with DetectorFailed
(invariant 7). A worker that reports a backend error (a fixed code) is kept. A worker that is
still loading is left loading: the request is blocked and a later one uses it.

After `failure_threshold` failures in a row (hangs, deaths, failed loads) the circuit opens:
every request is blocked at once for a backoff that doubles each time (up to
`max_backoff_seconds`), then one request tries again. Closed or open, the log says so without
any text, and `status()` feeds /healthz.

Parent and worker speak JSON over a pipe, with a size limit.
"""

import json
import logging
import multiprocessing
import queue
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
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
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_BACKOFF_SECONDS = 1.0
DEFAULT_MAX_BACKOFF_SECONDS = 60.0


class NerUnavailableError(Exception):
    """The NER workers could not load the backend at startup. Fixed message."""

    def __init__(self) -> None:
        super().__init__("the NER workers could not load the model")


class _LostError(Exception):
    """The worker hung, died, failed to load or broke the protocol: it must be replaced."""


class _NotReadyError(Exception):
    """The worker is still loading: this request is blocked, the worker is kept."""


@dataclass(slots=True, eq=False)
class _Worker:
    process: BaseProcess
    conn: Pipe
    spawned: float = field(default_factory=time.monotonic)
    ready: bool = False


class NerPool:
    """Worker processes running a NER backend, with one deadline per request."""

    def __init__(
        self,
        factory: str,
        options: Mapping[str, object] | None = None,
        *,
        workers: int = 1,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        load_timeout: float = DEFAULT_LOAD_TIMEOUT_SECONDS,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
    ) -> None:
        split_factory(factory)  # a malformed path fails here, not in a child process
        if workers < 1 or timeout <= 0 or load_timeout <= 0:
            raise ValueError("the NER pool needs at least one worker and positive time limits")
        if failure_threshold < 1 or backoff_seconds <= 0 or max_backoff_seconds < backoff_seconds:
            raise ValueError("the NER circuit breaker needs a threshold and positive backoffs")
        self._factory = factory
        self._options = json.dumps(dict(options or {}))
        self._size = workers
        self._timeout = timeout
        self._load_timeout = load_timeout
        self._threshold = failure_threshold
        self._backoff = backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._idle: queue.Queue[_Worker] = queue.Queue()
        self._workers: list[_Worker] = []
        self._busy: set[_Worker] = set()
        self._lock = threading.Lock()
        self._started = False
        self._closed = False
        self._failures = 0
        self._open_until = 0.0

    @property
    def timeout(self) -> float:
        """Time limit of one request (the engine uses it as the request deadline)."""
        return self._timeout

    # --- life cycle ---------------------------------------------------------------------------

    def start(self) -> None:
        """Start the workers and wait until every one has loaded the backend."""
        with self._lock:
            if self._started or self._closed:
                return
            workers = [self._spawn() for _ in range(self._size)]
        deadline = time.monotonic() + self._load_timeout
        failed = False
        for worker in workers:
            try:
                failed = failed or not self._wait_ready(worker, deadline)
            except _LostError:
                failed = True
        with self._lock:
            if failed:
                for worker in workers:
                    self._dispose(worker)
                raise NerUnavailableError()
            for worker in workers:
                self._idle.put(worker)
            self._started = True

    def close(self) -> None:
        """Stop every worker. A call in progress is blocked (its thread cleans its worker up)."""
        with self._lock:
            self._closed = True
            for worker in list(self._workers):
                if worker in self._busy:
                    self._kill(worker)  # never close a pipe another thread is reading
                    self._workers.remove(worker)
                else:
                    self._dispose(worker)

    def worker_pids(self) -> tuple[int | None, ...]:
        """Process ids of the current workers (for tests and diagnostics)."""
        with self._lock:
            return tuple(worker.process.pid for worker in self._workers)

    def status(self) -> str:
        """ "closed", "circuit_open", "starting" or "ok", for /healthz. Never any text.

        "starting": a worker was replaced and has not answered yet (it may be loading). The
        pipe is never read here: only the thread that holds a worker reads it.
        """
        with self._lock:
            if self._closed:
                return "closed"
            if time.monotonic() < self._open_until:
                return "circuit_open"
            if not self._started or not all(worker.ready for worker in self._workers):
                return "starting"
            return "ok"

    # --- workers ------------------------------------------------------------------------------

    def _spawn(self) -> _Worker:
        """A new worker, loading in the background. Called with the lock held."""
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

    @staticmethod
    def _kill(worker: _Worker) -> None:
        with suppress(ValueError):  # the process object was already closed
            worker.process.kill()

    def _dispose(self, worker: _Worker) -> None:
        """Kill a worker and free its pipe and process. Only by the thread that owns it."""
        if worker in self._workers:
            self._workers.remove(worker)
        worker.conn.close()
        self._kill(worker)
        with suppress(ValueError):
            worker.process.join(_JOIN_SECONDS)
            if worker.process.exitcode is not None:
                worker.process.close()

    def _read_ready(self, worker: _Worker) -> None:
        try:
            reply = json.loads(worker.conn.recv_bytes(_MAX_READY_BYTES))
        except (EOFError, OSError, ValueError):
            raise _LostError() from None
        if reply != {"status": "ready"}:
            raise _LostError()  # "load_failed": the model did not load
        worker.ready = True

    def _wait_ready(self, worker: _Worker, deadline: float) -> bool:
        """True once loaded; False if still loading at `deadline`; _LostError if it failed."""
        if worker.ready:
            return True
        load_deadline = worker.spawned + self._load_timeout
        try:
            arrived = worker.conn.poll(max(0.0, min(deadline, load_deadline) - time.monotonic()))
        except (EOFError, OSError):
            raise _LostError() from None
        if arrived:
            self._read_ready(worker)
            return True
        if time.monotonic() >= load_deadline:
            raise _LostError()  # it never finished loading
        return False

    # --- calls --------------------------------------------------------------------------------

    def _call(
        self,
        worker: _Worker,
        texts: Sequence[str],
        labels: Sequence[str],
        threshold: float,
        deadline: float,
    ) -> list[object] | None:
        """The results, or None for a backend error (worker kept). Raises _NotReadyError or
        _LostError (the worker must be replaced)."""
        if not self._wait_ready(worker, deadline):
            raise _NotReadyError()
        request = {"op": "predict", "texts": list(texts), "labels": list(labels)}
        payload = json.dumps({**request, "threshold": threshold}).encode()
        # The watchdog kills the worker at the deadline: a reply that stops half-way breaks
        # the pipe and recv_bytes returns, so the request never waits past the deadline.
        watchdog = threading.Timer(max(0.0, deadline - time.monotonic()), self._kill, (worker,))
        watchdog.daemon = True
        watchdog.start()
        try:
            worker.conn.send_bytes(payload)
            if not worker.conn.poll(max(0.0, deadline - time.monotonic())):
                logger.warning("NER worker went past the time limit: replaced")
                raise _LostError()
            reply = json.loads(worker.conn.recv_bytes(MAX_REPLY_BYTES))
        except (EOFError, OSError, ValueError, RecursionError):
            logger.warning("NER worker died or broke the protocol: replaced")
            raise _LostError() from None
        finally:
            watchdog.cancel()
        if reply == {"status": "error", "code": "predict_failed"}:
            return None
        ok = isinstance(reply, dict) and reply.get("status") == "ok"
        results = reply.get("results") if ok else None
        if not isinstance(results, list) or len(results) != len(texts):
            logger.warning("NER worker gave a malformed answer: replaced")
            raise _LostError()
        return results

    def _failed(self) -> None:
        """One more failure in a row; open the circuit past the threshold. Lock held."""
        self._failures += 1
        if self._failures >= self._threshold:
            backoff = min(
                self._backoff * 2 ** (self._failures - self._threshold), self._max_backoff
            )
            self._open_until = time.monotonic() + backoff
            logger.warning(
                "NER circuit open for %.1f s after %d worker failures in a row; requests are "
                "blocked meanwhile",
                backoff,
                self._failures,
            )

    def _succeeded(self) -> None:
        if self._failures >= self._threshold:
            logger.warning("NER circuit closed: the worker answers again")
        self._failures = 0
        self._open_until = 0.0

    def _release(self, worker: _Worker, lost: bool) -> None:
        with self._lock:
            self._busy.discard(worker)
            if self._closed:
                self._dispose(worker)
                return
            if lost:
                self._dispose(worker)
                self._failed()
                worker = self._spawn()  # it loads in the background
            self._idle.put(worker)

    def predict(
        self,
        texts: Sequence[str],
        labels: Sequence[str],
        threshold: float,
        deadline: float | None = None,
    ) -> object:
        """One list of raw entities per text (checked by the engine), or DetectorFailed.

        Everything, including waiting for a free or loading worker, happens before `deadline`
        (time.monotonic()) or `timeout` from now, whichever comes first.
        """
        now = time.monotonic()
        deadline = min(now + self._timeout, deadline if deadline is not None else now + 1e9)
        with self._lock:
            refused = not self._started or self._closed or now < self._open_until
        if refused:
            raise DetectorFailed()
        try:
            worker = self._idle.get(timeout=max(0.0, deadline - now))
        except queue.Empty:
            worker = None
        if worker is None:  # every worker stayed busy until the deadline
            raise DetectorFailed()
        with self._lock:
            if self._closed:
                self._dispose(worker)
                worker = None
            else:
                self._busy.add(worker)
        if worker is None:
            raise DetectorFailed()
        results: list[object] | None = None
        lost = True
        try:
            results = self._call(worker, texts, labels, threshold, deadline)
            lost = False
        except _NotReadyError:
            lost = False
        except _LostError:
            pass
        finally:
            self._release(worker, lost)
        if results is None:  # raised here, outside any except block: nothing is chained
            raise DetectorFailed()
        with self._lock:
            self._succeeded()
        return results
