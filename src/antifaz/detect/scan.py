"""Scan a text for personal data: validators and patterns (NER comes in issue 6)."""

from dataclasses import replace

from antifaz.detect.normalize import normalize
from antifaz.detect.overlaps import resolve
from antifaz.detect.patterns.identifiers import find_identifiers
from antifaz.detect.patterns.personal import find_patterns
from antifaz.detect.types import Span


def scan(text: str) -> list[Span]:
    """Non-overlapping spans of personal data in the text, sorted by position.

    The patterns run on a normalised view of the text (ADR-0014: invisible characters
    dropped, full-width and look-alike letters folded to ASCII) and the spans are mapped
    back, so they cover the original characters, invisible ones inside the value included.
    """
    view = normalize(text)
    spans = resolve([*find_identifiers(view.text), *find_patterns(view.text)])
    if view.text is text:
        return spans
    return [
        replace(span, start=view.original_start(span.start), end=view.original_end(span.end))
        for span in spans
    ]
