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


def resolve(spans: Iterable[Span]) -> list[Span]:
    """Keep the best non-overlapping spans, sorted by start. Spans that touch both stay.

    Kept spans never overlap each other, so they stay sorted by start and a candidate only
    needs checking against its two neighbours: O(n log n) even for a huge prompt full of
    identifiers (a quadratic check was a way to block the gateway).
    """
    starts: list[int] = []
    kept: list[Span] = []
    for candidate in sorted(set(spans), key=_priority):
        i = bisect_left(starts, candidate.start)
        if i > 0 and kept[i - 1].end > candidate.start:
            continue
        if i < len(kept) and kept[i].start < candidate.end:
            continue
        starts.insert(i, candidate.start)
        kept.insert(i, candidate)
    return kept
