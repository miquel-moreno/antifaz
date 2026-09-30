"""The NER layer of the detector: cache -> windows -> predictor (the pool) -> checked spans.

The texts it gets are the detector's normalised views (ADR-0014), so its spans use the same
offsets as the patterns and go back to the original text with the same map. Everything the
predictor answers is checked: one list per window, `[start, end, label, score]` with integer
offsets inside the window, a label that was asked for and a score between 0 and 1. Anything else
raises DetectorFailed: the request is blocked (invariant 7).
"""

import math
from collections.abc import Mapping, Sequence
from types import MappingProxyType

from antifaz.detect.ner.backend import Predictor
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
from antifaz.detect.types import Confidence, EntityType, Layer, Span
from antifaz.errors import DetectorFailed

DEFAULT_LABELS: Mapping[str, EntityType] = MappingProxyType(
    {"person": EntityType.PERSON, "address": EntityType.ADDRESS}
)
DEFAULT_THRESHOLD = 0.5
# Windows per call to the predictor: each call has its own time limit in the pool.
DEFAULT_BATCH_CHUNKS = 16


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
    ) -> None:
        if not labels:
            raise ValueError("the NER needs at least one label")
        if not 0 < threshold <= 1:
            raise ValueError("the NER threshold must be above 0 and at most 1")
        if batch_chunks < 1:
            raise ValueError("batch_chunks must be positive")
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
        self._chunker = f"{CHUNKER_VERSION}:{window}:{overlap}"

    def start(self) -> None:
        self._predictor.start()

    def close(self) -> None:
        self._predictor.close()

    def _key(self, text: str) -> bytes:
        return cache_key(
            text,
            model=self._model_id,
            labels=self._label_names,
            threshold=self._threshold,
            chunker=self._chunker,
        )

    def _predict(self, chunks: list[Chunk]) -> list[list[Entity]]:
        results: list[list[Entity]] = []
        for first in range(0, len(chunks), self._batch):
            batch = chunks[first : first + self._batch]
            raw = self._predictor.predict(
                [piece.text for piece in batch], self._label_names, self._threshold
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
            chunks = chunk(text, self._window, self._overlap)
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
