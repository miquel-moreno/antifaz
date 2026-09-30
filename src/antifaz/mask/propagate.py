"""Propagation of NER values to every place they appear in the request (ADR-0016).

A pattern or a validator finds every appearance of a value, but the NER may see a name in one
message and miss it in another. Then the egress guard would block the whole request, because
the hidden value would still be in the payload. So every value the NER found (and the policy
hides) is searched in all the texts of the request with the SAME rule as the guard:

- both sides normalised: NFKC, no format characters (Cf) or accents (Mn), the detector's
  Cyrillic and Greek look-alikes as Latin (`fold_homoglyphs`), casefold, runs of whitespace as
  one space;
- values with 6 or more letters and digits: compared with every other character removed and
  WITHOUT word boundaries ("Marina" is found inside "submarina": an accepted false positive);
- shorter values: compared with alphanumeric boundaries ("Ana" is not found in "semana").

The normalisation is done character by character, so every normalised character keeps the
offset of the original one it came from and a match maps back to an exact original range. The
guard normalises whole strings; the two agree on letters, digits, accents, look-alikes and
spaces (property test), but may differ on rare sequences whose NFKC form depends on the
neighbour characters (for example some Hangul jamo). There the guard sees a value the
propagation missed and BLOCKS the request: the mismatch fails closed, never open.

Only whole values are propagated: a NER span that does not start and end at a word boundary
(a fragment left by trimming around a DNI, or a model that cut a word) is not searched
elsewhere, and neither is a value with fewer than MIN_LETTERS letters and digits ("Al"). In
both cases the guard still blocks the request if the value appears again in clear. More than
MAX_PROPAGATED_VALUES different NER values in one request block it (DetectorFailed): the
search costs one pass over every text per value.

Plain `str.find` loops, no regular expressions (ADR-0008).
"""

import unicodedata
from collections.abc import Callable, Sequence
from functools import lru_cache

from antifaz.detect.normalize import fold_homoglyphs
from antifaz.detect.overlaps import resolve
from antifaz.detect.types import Confidence, EntityType, Layer, Span
from antifaz.errors import DetectorFailed

MIN_COMPACT = 6  # the guard's threshold: from this length on, no word boundaries
# Rounds of the short-value search; the guard still blocks anything left after them.
_MAX_ROUNDS = 8
MIN_LETTERS = 3  # shorter values are not propagated
MAX_PROPAGATED_VALUES = 200  # different NER values per request; more blocks the request


@lru_cache(maxsize=4096)
def _fold(char: str) -> str:
    """The normalised form of one character (may be empty or several characters)."""
    if char.isascii():
        return " " if char.isspace() else char.lower()
    if char.isspace():
        return " "
    decomposed = unicodedata.normalize("NFD", unicodedata.normalize("NFKC", char))
    kept = "".join(c for c in decomposed if unicodedata.category(c) not in ("Cf", "Mn"))
    return unicodedata.normalize("NFC", fold_homoglyphs(kept).casefold())


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
    if len(compact) < MIN_LETTERS:
        return None
    if len(compact) >= MIN_COMPACT:
        return compact, True
    return normal, False


def _whole(text: str, span: Span) -> bool:
    """The span starts and ends at a word boundary (not a fragment of a longer word)."""
    before = span.start == 0 or not text[span.start - 1].isalnum()
    after = span.end == len(text) or not text[span.end].isalnum()
    return before and after


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
        if span.layer is Layer.NER and hidden(span.type) and _whole(text, span)
    }
    if len(values) > MAX_PROPAGATED_VALUES:
        raise DetectorFailed()
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
