"""Normalised view of a text for the detector, with a map back to the original offsets.

Before the patterns run (ADR-0014), every character that only hides a value is folded:

- invisible format characters (Unicode category Cf: zero-width space, ZWNJ/ZWJ, soft
  hyphen, word joiner, BOM, bidi marks...) are dropped;
- characters whose NFKC form is a single ASCII letter or digit (full-width digits and
  letters, mathematical letters, superscripts) become that ASCII character;
- odd spaces (tab, no-break space, thin and other Unicode spaces) become a plain space;
- a small table of Cyrillic and Greek letters that look exactly like Latin ones become the
  Latin letter (HOMOGLYPHS below).

Only whole characters are replaced by one character or dropped, so a span found in the
normalised text maps back to the original with a monotonic offset map: the masked range
covers the original characters, including invisible ones inside the value. The text itself
is never changed; restore() gives back the exact original.
"""

import unicodedata
from bisect import bisect_right
from collections.abc import Iterable

# Cyrillic and Greek capitals and lower case letters that render like a Latin letter.
HOMOGLYPHS: dict[str, str] = {
    # Cyrillic
    "\u0410": "A", "\u0412": "B", "\u0415": "E", "\u041a": "K", "\u041c": "M",
    "\u041d": "H", "\u041e": "O", "\u0420": "P", "\u0421": "C", "\u0422": "T",
    "\u0425": "X", "\u0423": "Y", "\u0417": "Z",
    "\u0430": "a", "\u0432": "b", "\u0435": "e", "\u043a": "k", "\u043c": "m",
    "\u043d": "h", "\u043e": "o", "\u0440": "p", "\u0441": "c", "\u0442": "t",
    "\u0445": "x", "\u0443": "y", "\u0437": "z",
    # Greek
    "\u0391": "A", "\u0392": "B", "\u0395": "E", "\u0396": "Z", "\u0397": "H",
    "\u0399": "I", "\u039a": "K", "\u039c": "M", "\u039d": "N", "\u039f": "O",
    "\u03a1": "P", "\u03a4": "T", "\u03a5": "Y", "\u03a7": "X",
    "\u03b1": "a", "\u03b2": "b", "\u03b5": "e", "\u03b6": "z", "\u03b7": "h",
    "\u03b9": "i", "\u03ba": "k", "\u03bc": "m", "\u03bd": "n", "\u03bf": "o",
    "\u03c1": "p", "\u03c4": "t", "\u03c5": "y", "\u03c7": "x",
}  # fmt: skip

# Spaces that become a plain space (line breaks are kept: they are handled by the patterns).
_SPACES = "\t\u00a0\u1680\u202f\u205f\u3000" + "".join(map(chr, range(0x2000, 0x200B)))

# Ordinal indicators stay: addresses use them ("3.\u00ba 2.\u00aa") and they never hide an id.
_KEPT = frozenset("\u00aa\u00ba")

# Blocks searched for Cf characters and for NFKC forms that are one ASCII letter or digit.
_CF_RANGES = ((0x0000, 0x3000), (0xFE00, 0x10000), (0x1D100, 0x1D200), (0xE0000, 0xE0080))
_NFKC_RANGES = ((0x00A0, 0x0100), (0x2070, 0x20A0), (0x2100, 0x2150), (0x2460, 0x24F0),
                (0xFF00, 0xFF70), (0x1D400, 0x1D800))  # fmt: skip


def _codepoints(ranges: Iterable[tuple[int, int]]) -> Iterable[str]:
    for low, high in ranges:
        for code in range(low, high):
            yield chr(code)


def _build() -> tuple[dict[int, str | None], str]:
    table: dict[int, str | None] = {}
    for char in _codepoints(_NFKC_RANGES):
        folded = unicodedata.normalize("NFKC", char)
        if char in _KEPT:
            continue
        if len(folded) == 1 and folded.isascii() and folded.isalnum() and folded != char:
            table[ord(char)] = folded
    table.update({ord(char): latin for char, latin in HOMOGLYPHS.items()})
    table.update({ord(char): " " for char in _SPACES})
    dropped = "".join(c for c in _codepoints(_CF_RANGES) if unicodedata.category(c) == "Cf")
    table.update({ord(char): None for char in dropped})
    return table, dropped


_TABLE, _DROPPED = _build()
_DROPPED_SET = frozenset(_DROPPED)


class Normalized:
    """The normalised text and the map from its offsets back to the original text."""

    __slots__ = ("_shift", "text")

    def __init__(self, original: str) -> None:
        ascii_plain = original.isascii() and "\t" not in original
        self.text = original if ascii_plain else original.translate(_TABLE)
        # For each dropped character, the normalised offset where it would have been. The
        # list is sorted, so the original offset of a normalised one is found by bisection.
        self._shift: list[int] = []
        if len(self.text) != len(original):
            removed = 0
            for index, char in enumerate(original):
                if char in _DROPPED_SET:
                    self._shift.append(index - removed)
                    removed += 1

    def original_start(self, start: int) -> int:
        """Original offset of the character at normalised offset `start`."""
        return start + bisect_right(self._shift, start)

    def original_end(self, end: int) -> int:
        """Original offset just after the character before normalised offset `end` (> 0)."""
        return self.original_start(end - 1) + 1


def normalize(text: str) -> Normalized:
    """The normalised view of `text` for the detector (the text itself is not changed)."""
    return Normalized(text)
