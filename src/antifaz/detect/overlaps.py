"""Overlap resolution: validator beats pattern beats NER, then longer, then earlier, then type
order. A partial-overlap loser is trimmed to what nobody else covers (ADR-0016)."""

from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import replace

from antifaz.detect.types import EntityType, Layer, Span

_LAYER_ORDER = {Layer.VALIDATOR: 0, Layer.PATTERN: 1, Layer.NER: 2}
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


def _free_pieces(candidate: Span, starts: list[int], kept: list[Span]) -> list[Span]:
    """The parts of `candidate` that no kept span covers, as spans of the same kind."""
    i = bisect_left(starts, candidate.start)
    if i > 0 and kept[i - 1].end > candidate.start:
        i -= 1
    pieces: list[Span] = []
    position = candidate.start
    while i < len(kept) and kept[i].start < candidate.end:
        if kept[i].start > position:
            pieces.append(replace(candidate, start=position, end=kept[i].start))
        position = max(position, kept[i].end)
        i += 1
    if position < candidate.end:
        pieces.append(replace(candidate, start=position, end=candidate.end))
    return pieces


def _has_alnum(text: str | None, span: Span) -> bool:
    if text is None:
        return True
    return any(char.isalnum() for char in text[span.start : span.end])


def resolve(spans: Iterable[Span], text: str | None = None) -> list[Span]:
    """Non-overlapping spans, sorted by start, covering everything the input spans covered.

    Validator and pattern spans are placed first, so the NER never changes them (ADR-0016).
    Among them, a span that contains another one entirely wins, whatever its layer (ADR-0010:
    masking more is the safe failure): spans that are not inside another one are placed
    first, so a container always beats what it contains. The same holds among NER spans. For
    partial overlaps the priority order picks the winner, and the loser is TRIMMED to the
    parts nobody else covers (ADR-0016), so no detected letter or digit is left in clear: a
    NER "name" around a DNI becomes the name without the DNI, and the DNI. With `text`,
    trimmed pieces without any letter or digit (a space, a dash) are dropped. Spans that only
    touch are both kept.

    Kept spans never overlap each other, so they stay sorted by start and a candidate is only
    compared with the kept spans it overlaps (found by bisection). The pieces of one candidate
    are bounded by the layers and patterns that overlap it: a huge prompt full of identifiers
    is still resolved quickly (a quadratic check was a way to block the gateway).
    """
    unique = set(spans)
    ner = {span for span in unique if span.layer is Layer.NER}
    # The container rule only works inside the same side: validators and patterns among
    # themselves, NER spans among themselves. A NER span never swallows a validator or pattern
    # span (a policy that allows names would then send a DNI inside a "name" in clear).
    inside = _contained(unique - ner) | _contained(ner)
    starts: list[int] = []
    kept: list[Span] = []
    order = sorted(unique, key=lambda s: (s.layer is Layer.NER, s in inside, *_priority(s)))
    for candidate in order:
        pieces = _free_pieces(candidate, starts, kept)
        if len(pieces) == 1 and pieces[0] == candidate:
            keep = [candidate]  # untouched: kept even without letters (validators decided)
        else:
            keep = [piece for piece in pieces if _has_alnum(text, piece)]
        for piece in keep:
            i = bisect_left(starts, piece.start)
            starts.insert(i, piece.start)
            kept.insert(i, piece)
    return kept
