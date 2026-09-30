"""Scan a text for personal data: validators, patterns and, optionally, the NER (ADR-0016)."""

from collections.abc import Iterable, Sequence
from dataclasses import replace

from antifaz.detect.ner.engine import NerDetector
from antifaz.detect.normalize import Normalized, normalize
from antifaz.detect.overlaps import resolve
from antifaz.detect.patterns.identifiers import find_identifiers
from antifaz.detect.patterns.personal import find_patterns
from antifaz.detect.types import Span


def _resolved(view: Normalized, original: str, extra: Iterable[Span] = ()) -> list[Span]:
    spans = resolve([*find_identifiers(view.text), *find_patterns(view.text), *extra], view.text)
    if view.text is original:
        return spans
    return [
        replace(span, start=view.original_start(span.start), end=view.original_end(span.end))
        for span in spans
    ]


def scan(text: str) -> list[Span]:
    """Non-overlapping spans of personal data in the text, sorted by position.

    The patterns run on a normalised view of the text (ADR-0014: invisible characters
    dropped, full-width and look-alike letters folded to ASCII) and the spans are mapped
    back, so they cover the original characters, invisible ones inside the value included.
    """
    return _resolved(normalize(text), text)


class Scanner:
    """scan() plus the NER layer, which reads the same normalised views (ADR-0016).

    `scan_many` sends every text of a request to the NER in one go (one call to the process
    pool instead of one per string). Without `ner`, it is exactly scan().
    """

    def __init__(self, ner: NerDetector | None = None) -> None:
        self._ner = ner

    def __call__(self, text: str) -> list[Span]:
        return self.scan_many([text])[0]

    def scan_many(self, texts: Sequence[str]) -> list[list[Span]]:
        views = [normalize(text) for text in texts]
        if self._ner is None:
            return [_resolved(view, text) for view, text in zip(views, texts, strict=True)]
        found = self._ner.find_many([view.text for view in views])
        return [
            _resolved(view, text, extra)
            for view, text, extra in zip(views, texts, found, strict=True)
        ]
