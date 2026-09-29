"""Context: is there a keyword (e.g. "pasaporte") just before a candidate value?"""

import re


def has_context(text: str, start: int, keywords: re.Pattern[str], window: int = 40) -> bool:
    """True if `keywords` matches within the `window` characters before `start`.

    The search uses pos/endpos instead of slicing the text, so a word boundary is decided on
    the real text: the "tel" at the end of "hotel" is not a keyword even if the window
    starts in the middle of the word.
    """
    return keywords.search(text, max(0, start - window), start) is not None
