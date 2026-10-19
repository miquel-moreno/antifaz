"""The NER layer of the detector: cache -> windows -> predictor (the pool) -> checked spans.

The texts it gets are the detector's normalised views (ADR-0014), so its spans use the same
offsets as the patterns and go back to the original text with the same map. Everything the
predictor answers is checked: one list per window, `[start, end, label, score]` with integer
offsets inside the window, a label that was asked for and a score between 0 and 1. Anything else
raises DetectorFailed: the request is blocked (invariant 7). So does a request with more than
`max_chunks` windows, or one whose windows do not all fit in ONE deadline (the pool's time limit
by default): the time limit is per request, not per call.
"""

import math
import time
from collections.abc import Mapping, Sequence
from types import MappingProxyType

from antifaz.detect.ner.backend import NerBusyError, Predictor
from antifaz.detect.ner.cache import SpanCache, cache_key
from antifaz.detect.ner.chunker import (
    CHUNKER_VERSION,
    OVERLAP_TOKENS,
    WINDOW_TOKENS,
    Chunk,
    Entity,
    chunk,
    merge,
)
from antifaz.detect.ner.view import VIEW_VERSION, ner_view
from antifaz.detect.types import Confidence, EntityType, Layer, Span
from antifaz.errors import DetectorFailed

DEFAULT_LABELS: Mapping[str, EntityType] = MappingProxyType(
    {"person": EntityType.PERSON, "address": EntityType.ADDRESS}
)
DEFAULT_THRESHOLD = 0.5
# Windows per call to the predictor.
DEFAULT_BATCH_CHUNKS = 16
# Windows per request: a bigger request is blocked before the model sees it (about 1 MB of
# text; a real model needs tens of milliseconds per window on a CPU).
DEFAULT_MAX_CHUNKS = 1024


def _entities(raw: object, length: int, labels: Mapping[str, EntityType]) -> list[Entity] | None:
    """The entities of one window, or None if the answer is malformed."""
    if not isinstance(raw, list | tuple):
        return None
    out: list[Entity] = []
    for item in raw:
        if not isinstance(item, list | tuple) or len(item) != 4:
            return None
        start, end, label, score = item
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= length:
            return None
        if not isinstance(label, str) or label not in labels:
            return None
        if type(score) not in (int, float) or not 0 <= score <= 1 or math.isnan(score):
            return None
        out.append((start, end, label, float(score)))
    return out


class _TryPredictor:
    """The predictor of a for_try() detector: try_predict() if the inner one has it."""

    def __init__(self, inner: Predictor) -> None:
        self._inner = inner
        self.busy = False
        self.too_long = False

    def start(self) -> None:  # pragma: no cover - a Try detector is never started
        raise RuntimeError("a Try detector is never started")

    def close(self) -> None:  # pragma: no cover - nor closed: the gateway owns the predictor
        raise RuntimeError("a Try detector is never closed")

    def predict(
        self,
        texts: Sequence[str],
        labels: Sequence[str],
        threshold: float,
        deadline: float | None = None,
    ) -> object:
        try_predict = getattr(self._inner, "try_predict", None)
        if not callable(try_predict):
            return self._inner.predict(texts, labels, threshold, deadline=deadline)
        try:
            return try_predict(texts, labels, threshold, deadline=deadline)
        except NerBusyError:
            self.busy = True
            raise


class NerDetector:
    """Finds names and addresses through a predictor, with a cache in front of it."""

    def __init__(
        self,
        predictor: Predictor,
        *,
        labels: Mapping[str, EntityType] = DEFAULT_LABELS,
        threshold: float = DEFAULT_THRESHOLD,
        cache: SpanCache | None = None,
        model_id: str = "unknown",
        window: int = WINDOW_TOKENS,
        overlap: int = OVERLAP_TOKENS,
        batch_chunks: int = DEFAULT_BATCH_CHUNKS,
        max_chunks: int = DEFAULT_MAX_CHUNKS,
        timeout: float | None = None,
    ) -> None:
        if not labels:
            raise ValueError("the NER needs at least one label")
        if not 0 < threshold <= 1:
            raise ValueError("the NER threshold must be above 0 and at most 1")
        if batch_chunks < 1 or max_chunks < 1:
            raise ValueError("batch_chunks and max_chunks must be positive")
        # Time for the whole request: by default the predictor's (the pool's) time limit.
        timeout = timeout if timeout is not None else getattr(predictor, "timeout", None)
        if timeout is not None and timeout <= 0:
            raise ValueError("the NER time limit must be positive")
        chunk("", window, overlap)  # checks the window settings
        self._predictor = predictor
        self._labels = dict(labels)
        self._label_names = tuple(sorted(self._labels))
        self._threshold = threshold
        self._cache = cache
        self._model_id = model_id
        self._window = window
        self._overlap = overlap
        self._batch = batch_chunks
        self._max_chunks = max_chunks
        self._timeout: float | None = timeout
        self._chunker = f"{CHUNKER_VERSION}:{window}:{overlap}:{VIEW_VERSION}"

    def for_try(
        self, seconds: float, *, max_chunks: int | None = None, batch_chunks: int | None = None
    ) -> "NerDetector":
        """A detector for «Prueba un texto» (PR 11d-a review) over the same predictor:
        - no span cache: a Try never reads or writes the proxy's cache, so it cannot tell
          whether a text went through the proxy (a cache hit is fast), never evicts the proxy's
          entries and leaves no digest or spans behind;
        - a deadline of at most `seconds`, and work sized to it: at most `max_chunks` windows
          (more is refused BEFORE any worker is asked; was_too_long() says so) in batches of
          `batch_chunks`, each one a separate try_predict (the pool lets a waiting proxy
          request go first between batches);
        - the predictor's try_predict() when it has one (the pool: no waiting for a worker and
          no circuit breaker); in-process predictors have no workers to starve and are called
          as usual. was_busy() says whether this detector found no free worker.
        One per Try: the busy flag belongs to it."""
        if seconds <= 0:
            raise ValueError("the Try deadline must be positive")
        if (max_chunks is not None and max_chunks < 1) or (
            batch_chunks is not None and batch_chunks < 1
        ):
            raise ValueError("the Try chunk budget and batch size must be positive")
        timeout = seconds if self._timeout is None else min(seconds, self._timeout)
        return NerDetector(
            _TryPredictor(self._predictor),
            labels=self._labels,
            threshold=self._threshold,
            cache=None,
            model_id=self._model_id,
            window=self._window,
            overlap=self._overlap,
            batch_chunks=batch_chunks or self._batch,
            max_chunks=min(max_chunks or self._max_chunks, self._max_chunks),
            timeout=timeout,
        )

    def was_busy(self) -> bool:
        """For a detector from for_try(): whether it found no free NER worker."""
        return bool(getattr(self._predictor, "busy", False))

    def was_too_long(self) -> bool:
        """For a detector from for_try(): whether the text needed more windows than its budget
        (refused before any worker was asked)."""
        return bool(getattr(self._predictor, "too_long", False))

    def start(self) -> None:
        self._predictor.start()

    def close(self) -> None:
        self._predictor.close()

    def status(self) -> str:
        """The predictor's state for /healthz ("ok" if it does not say)."""
        status = getattr(self._predictor, "status", None)
        return str(status()) if callable(status) else "ok"

    def _key(self, text: str) -> bytes:
        return cache_key(
            text,
            model=self._model_id,
            labels=self._label_names,
            threshold=self._threshold,
            chunker=self._chunker,
        )

    def _predict(self, chunks: list[Chunk]) -> list[list[Entity]]:
        """Every window through the predictor, all under ONE deadline for the request."""
        if len(chunks) > self._max_chunks:
            if isinstance(self._predictor, _TryPredictor):
                self._predictor.too_long = True  # before any call: no worker is touched
            raise DetectorFailed()
        deadline = None if self._timeout is None else time.monotonic() + self._timeout
        results: list[list[Entity]] = []
        for first in range(0, len(chunks), self._batch):
            batch = chunks[first : first + self._batch]
            texts = [piece.text for piece in batch]
            if deadline is None:
                raw = self._predictor.predict(texts, self._label_names, self._threshold)
            elif time.monotonic() >= deadline:
                raise DetectorFailed()
            else:
                raw = self._predictor.predict(
                    texts, self._label_names, self._threshold, deadline=deadline
                )
            if not isinstance(raw, list | tuple) or len(raw) != len(batch):
                raise DetectorFailed()
            for piece, answer in zip(batch, raw, strict=True):
                entities = _entities(answer, len(piece.text), self._labels)
                if entities is None:
                    raise DetectorFailed()
                results.append([e for e in entities if e[3] >= self._threshold])
        return results

    def _spans(self, entities: list[Entity]) -> tuple[Span, ...]:
        return tuple(
            Span(start, end, self._labels[label], Layer.NER, Confidence.MEDIUM)
            for start, end, label, _ in entities
        )

    def find_many(self, texts: Sequence[str]) -> list[list[Span]]:
        """NER spans for each text, sorted by start (they may overlap: resolve() decides)."""
        found: dict[str, tuple[Span, ...]] = {}
        keys: dict[str, bytes] = {}
        pending: list[tuple[str, list[Chunk]]] = []
        for text in dict.fromkeys(texts):  # each different text once, in order
            if self._cache is not None:
                keys[text] = self._key(text)
                cached = self._cache.get(keys[text])
                if cached is not None:
                    found[text] = cached
                    continue
            # The model reads the view without blobs or long glued numbers (view.py).
            chunks = chunk(ner_view(text), self._window, self._overlap)
            if chunks:
                pending.append((text, chunks))
            else:
                found[text] = ()
        all_chunks = [piece for _, chunks in pending for piece in chunks]
        results = iter(self._predict(all_chunks))
        for text, chunks in pending:
            spans = self._spans(merge(chunks, [next(results) for _ in chunks]))
            found[text] = spans
            if self._cache is not None:
                self._cache.put(keys[text], spans)
        return [list(found[text]) for text in texts]
