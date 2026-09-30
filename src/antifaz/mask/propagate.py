"""Propagation of NER values to every place they appear in the request (ADR-0016).

A pattern or a validator finds every appearance of a value, but the NER may see a name in one
message and miss it in another. Then the egress guard would block the whole request, because
the hidden value would still be in the payload. So every value the NER found (and the policy
hides) is searched in all the texts of the request with the SAME rule as the guard:

- both sides normalised: NFKC, no format characters (Cf) or accents (Mn), casefold, runs of
  whitespace as one space;
- values with 6 or more letters and digits: compared with every other character removed and
  WITHOUT word boundaries ("Marina" is found inside "submarina": an accepted false positive);
- shorter values: compared with alphanumeric boundaries ("Ana" is not found in "semana").

The normalisation is done character by character, so every normalised character keeps the
offset of the original one it came from and a match maps back to an exact original range.
Plain `str.find` loops, no regular expressions (ADR-0008).
"""

import unicodedata
from collections.abc import Callable, Sequence
from functools import lru_cache

from antifaz.detect.overlaps import resolve
from antifaz.detect.types import Confidence, EntityType, Layer, Span

MIN_COMPACT = 6  # the guard's threshold: from this length on, no word boundaries
# Rounds of the short-value search; the guard still blocks anything left after them.
_MAX_ROUNDS = 8


@lru_cache(maxsize=4096)
def _fold(char: str) -> str:
    """The normalised form of one character (may be empty or several characters)."""
    if char.isascii():
        return " " if char.isspace() else char.lower()
    if char.isspace():
        return " "
    decomposed = unicodedata.normalize("NFD", unicodedata.normalize("NFKC", char))
    kept = "".join(c for c in decomposed if unicodedata.category(c) not in ("Cf", "Mn"))
    return unicodedata.normalize("NFC", kept.casefold())


class _Index:
    """A text normalised like the guard does, with the original offset of each character."""

    def __init__(self, text: str) -> None:
        normal: list[str] = []
        origin: list[int] = []
        for index, char in enumerate(text):
            for folded in _fold(char):
                if folded == " " and (not normal or normal[-1] == " "):
                    continue  # whitespace runs collapse; leading whitespace is dropped
                normal.append(folded)
                origin.append(index)
        self.normal = "".join(normal)
        self._origin = origin
        alnum = [i for i, char in enumerate(normal) if char.isalnum()]
        self.compact = "".join(normal[i] for i in alnum)
        self._compact_origin = [origin[i] for i in alnum]

    def _range(self, origin: list[int], start: int, length: int) -> tuple[int, int]:
        return origin[start], origin[start + length - 1] + 1

    def find_compact(self, needle: str) -> list[tuple[int, int]]:
        found = []
        start = self.compact.find(needle)
        while start != -1:
            found.append(self._range(self._compact_origin, start, len(needle)))
            start = self.compact.find(needle, start + 1)
        return found

    def _boundary(self, index: int, masked: frozenset[int]) -> bool:
        """No letter or digit at normalised `index`, or one that is going to be masked (the
        guard then sees the "[[" or "]]" of a placeholder there)."""
        if not 0 <= index < len(self.normal):
            return True
        return not self.normal[index].isalnum() or self._origin[index] in masked

    def find_word(self, needle: str, masked: frozenset[int]) -> list[tuple[int, int]]:
        found = []
        start = self.normal.find(needle)
        while start != -1:
            end = start + len(needle)
            if self._boundary(start - 1, masked) and self._boundary(end, masked):
                found.append(self._range(self._origin, start, len(needle)))
            start = self.normal.find(needle, start + 1)
        return found


def _needle(value: str) -> tuple[str, bool] | None:
    """(what to search, whether it is the compact form), or None for a value without text."""
    normal = _Index(value).normal.strip()
    compact = "".join(char for char in normal if char.isalnum())
    if not compact:
        return None
    if len(compact) >= MIN_COMPACT:
        return compact, True
    return normal, False


def propagate(
    texts: Sequence[str],
    spans: Sequence[Sequence[Span]],
    hidden: Callable[[EntityType], bool],
) -> list[list[Span]]:
    """`spans` plus a span at every other appearance of each hidden NER value, resolved."""
    values = {
        (span.type, text[span.start : span.end])
        for text, found in zip(texts, spans, strict=True)
        for span in found
        if span.layer is Layer.NER and hidden(span.type)
    }
    needles = [(entity, needle) for entity, value in values if (needle := _needle(value))]
    if not needles:
        return [list(found) for found in spans]
    long = [(entity, needle) for entity, (needle, compact) in needles if compact]
    short = [(entity, needle) for entity, (needle, compact) in needles if not compact]
    return [
        _propagate_one(text, found, long, short, hidden)
        for text, found in zip(texts, spans, strict=True)
    ]


def _masked(spans: Sequence[Span], hidden: Callable[[EntityType], bool]) -> frozenset[int]:
    return frozenset(i for s in spans if hidden(s.type) for i in range(s.start, s.end))


def _propagate_one(
    text: str,
    found: Sequence[Span],
    long: list[tuple[EntityType, str]],
    short: list[tuple[EntityType, str]],
    hidden: Callable[[EntityType], bool],
) -> list[Span]:
    index = _Index(text)
    extra = [
        Span(start, end, entity, Layer.NER, Confidence.MEDIUM)
        for entity, needle in long
        for start, end in index.find_compact(needle)
    ]
    current = resolve([*found, *extra], text) if extra else list(found)
    # A short value glued to a masked one gets a boundary once masked ("[[PERSON_1]]Ana" is
    # what the guard sees): search again until nothing new is masked (a few rounds at most).
    for _ in range(_MAX_ROUNDS if short else 0):
        masked = _masked(current, hidden)
        extra = [
            Span(start, end, entity, Layer.NER, Confidence.MEDIUM)
            for entity, needle in short
            for start, end in index.find_word(needle, masked)
        ]
        updated = resolve([*current, *extra], text) if extra else current
        if _masked(updated, hidden) == masked:
            return updated
        current = updated
    return current
