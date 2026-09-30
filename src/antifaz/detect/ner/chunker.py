"""Overlapping windows of a text for the NER, and the way back to the whole text (ADR-0016).

GLiNER reads at most 384 words and cuts the rest WITHOUT warning, so a name in word 400 would
never be seen. The text is cut into windows of WINDOW_TOKENS tokens that overlap by
OVERLAP_TOKENS: an entity shorter than the overlap that crosses a border is whole in the next
window. A token is a run of up to MAX_TOKEN_CHARS letters or digits, or a single sign, so
there are always at least as many of our tokens as GLiNER words: a window never goes past it.

Offsets found in a window are shifted back to the text, and entities of the same label that
overlap (the same name seen in two windows, or cut at a window edge) are joined.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

WINDOW_TOKENS = 200
OVERLAP_TOKENS = 50
MAX_TOKEN_CHARS = 40
# Part of the cache key: change it whenever the windows change.
CHUNKER_VERSION = f"1:{WINDOW_TOKENS}:{OVERLAP_TOKENS}:{MAX_TOKEN_CHARS}"

# One alternative per character class, fixed maximum length: nothing to backtrack (ADR-0008).
_TOKEN = re.compile(rf"\w{{1,{MAX_TOKEN_CHARS}}}|[^\w\s]")

Entity = tuple[int, int, str, float]  # start, end, label, score


@dataclass(frozen=True, slots=True)
class Chunk:
    """A window: its offset in the text and its text (from the first to the last token)."""

    start: int
    text: str


def token_count(text: str) -> int:
    return sum(1 for _ in _TOKEN.finditer(text))


def chunk(text: str, window: int = WINDOW_TOKENS, overlap: int = OVERLAP_TOKENS) -> list[Chunk]:
    """Overlapping windows covering every token of `text` (none for a blank text)."""
    if window < 1 or not 0 <= overlap < window:
        raise ValueError("window must be positive and larger than the overlap")
    tokens = [(match.start(), match.end()) for match in _TOKEN.finditer(text)]
    chunks: list[Chunk] = []
    first = 0
    while first < len(tokens):
        last = min(first + window, len(tokens))
        start, end = tokens[first][0], tokens[last - 1][1]
        chunks.append(Chunk(start, text[start:end]))
        if last == len(tokens):
            break
        first += window - overlap
    return chunks


def merge(chunks: Sequence[Chunk], results: Sequence[Sequence[Entity]]) -> list[Entity]:
    """Entities of every window in text offsets, overlapping ones of the same label joined."""
    shifted = sorted(
        (piece.start + start, piece.start + end, label, score)
        for piece, entities in zip(chunks, results, strict=True)
        for start, end, label, score in entities
    )
    by_label: dict[str, list[Entity]] = {}
    for start, end, label, score in shifted:
        joined = by_label.setdefault(label, [])
        if joined and start < joined[-1][1]:
            last = joined[-1]
            joined[-1] = (last[0], max(last[1], end), label, max(last[3], score))
        else:
            joined.append((start, end, label, score))
    return sorted(entity for entities in by_label.values() for entity in entities)
