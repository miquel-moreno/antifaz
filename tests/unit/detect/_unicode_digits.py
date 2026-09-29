"""Rewrite ASCII digits with look-alike Unicode digits, without typing them in the source."""


def _table(zero: int) -> dict[int, int]:
    return str.maketrans("0123456789", "".join(chr(zero + i) for i in range(10)))


FULL_WIDTH = _table(0xFF10)  # U+FF10..U+FF19
ARABIC_INDIC = _table(0x0660)  # U+0660..U+0669
EXTENDED_ARABIC_INDIC = _table(0x06F0)  # U+06F0..U+06F9 (Persian)


def full_width(value: str) -> str:
    return value.translate(FULL_WIDTH)


def arabic_indic(value: str) -> str:
    return value.translate(ARABIC_INDIC)


def persian(value: str) -> str:
    return value.translate(EXTENDED_ARABIC_INDIC)
