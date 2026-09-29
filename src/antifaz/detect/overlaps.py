"""Overlap resolution: validator beats pattern, then longer, then earlier, then type order."""

from bisect import bisect_left
from collections.abc import Iterable

from antifaz.detect.types import EntityType, Layer, Span

_LAYER_ORDER = {Layer.VALIDATOR: 0, Layer.PATTERN: 1}
_TYPE_ORDER = {entity_type: i for i, entity_type in enumerate(EntityType)}


def _priority(span: Span) -> tuple[int, int, int, int, str]:
    return (
        _LAYER_ORDER[span.layer],
        -(span.end - span.start),
        span.start,
        _TYPE_ORDER[span.type],
        span.confidence.value,  # only to make the order total, so the result is stable
    )


def _contained(spans: set[Span]) -> set[Span]:
    """Spans strictly inside another span, in O(n log n).

    Sorted by start and then by longest first, a span is inside an earlier one exactly when
    it does not reach past the furthest end seen so far (identical ranges do not count, so
    the priority rules choose between them).
    """
    inside: set[Span] = set()
    furthest: tuple[int, int] | None = None  # (start, end) of the span reaching furthest
    for span in sorted(spans, key=lambda s: (s.start, -s.end)):
        if furthest is not None and span.end <= furthest[1]:
            if (span.start, span.end) != furthest:
                inside.add(span)
        else:
            furthest = (span.start, span.end)
    return inside


def resolve(spans: Iterable[Span]) -> list[Span]:
    """Keep the best non-overlapping spans, sorted by start. Spans that touch both stay.

    A span that contains another one entirely wins, whatever its layer (ADR-0010: masking
    more is the safe failure): spans that are not inside another one are placed first, so a
    container always beats what it contains. If the container itself loses to a partial
    overlap, the spans inside it compete again, so no detected text is left uncovered.
    For partial overlaps the priority order applies. Kept spans never overlap each other,
    so they stay sorted by start and a candidate only needs checking against its two
    neighbours: O(n log n) even for a huge prompt full of identifiers (a quadratic check was
    a way to block the gateway).
    """
    unique = set(spans)
    inside = _contained(unique)
    starts: list[int] = []
    kept: list[Span] = []
    for candidate in sorted(unique, key=lambda s: (s in inside, *_priority(s))):
        i = bisect_left(starts, candidate.start)
        if i > 0 and kept[i - 1].end > candidate.start:
            continue
        if i < len(kept) and kept[i].start < candidate.end:
            continue
        starts.insert(i, candidate.start)
        kept.insert(i, candidate)
    return kept
