"""Overlap resolution: validator beats pattern, then longer, then earlier, then type order."""

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


def _overlaps(a: Span, b: Span) -> bool:
    return a.start < b.end and b.start < a.end


def resolve(spans: Iterable[Span]) -> list[Span]:
    """Keep the best non-overlapping spans, sorted by start. Spans that touch both stay."""
    kept: list[Span] = []
    for candidate in sorted(set(spans), key=_priority):
        if not any(_overlaps(candidate, span) for span in kept):
            kept.append(candidate)
    return sorted(kept, key=lambda span: (span.start, span.end))
