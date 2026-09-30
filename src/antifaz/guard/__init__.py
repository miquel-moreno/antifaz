"""Egress guard: a second check on the final bytes before they leave for a provider.

Looks for any value the policy said to hide. If the payload is JSON, it checks every
decoded string, key and number (so `\\u` escapes are covered); otherwise the raw text.
Both the value and the text are normalised the same way: NFKC, format characters (Cf)
removed, accents removed, Cyrillic and Greek look-alikes folded to Latin (the detector's table,
`fold_homoglyphs`), casefold and whitespace runs collapsed. Values with 6 or more
letters and digits are then compared with every other character removed and WITHOUT word
boundaries, so a value glued to other letters is still caught. Shorter values need
alphanumeric boundaries, so "Ana" does not match inside "semana". Any match blocks the
request, false positives included: the guard fails closed on purpose.

Plain `str.find` loops, no regular expressions (ADR-0008).

Forbidden: It cannot be disabled.
"""

import json
import unicodedata
from collections.abc import Iterator

from antifaz.detect.normalize import fold_homoglyphs
from antifaz.errors import EgressBlocked
from antifaz.vault import Vault

_MIN_COMPACT = 6  # from this length on, a value is searched without boundaries


_ASCII_NOT_ALNUM = str.maketrans(
    "", "", "".join(chr(c) for c in range(128) if not chr(c).isalnum())
)


def _normal(text: str) -> str:
    if text.isascii():  # fast path: NFKC, Cf and Mn change nothing in ASCII
        return " ".join(text.lower().split())
    text = unicodedata.normalize("NFKC", text)
    text = "".join(
        char
        for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) not in ("Cf", "Mn")
    )
    text = unicodedata.normalize("NFC", fold_homoglyphs(text).casefold())
    return " ".join(text.split())


def _compact(normal: str) -> str:
    if normal.isascii():
        return normal.translate(_ASCII_NOT_ALNUM)
    return "".join(char for char in normal if char.isalnum())


def _boundary(text: str, index: int) -> bool:
    """True when there is no letter or digit at text[index] (or it is outside the text)."""
    return not 0 <= index < len(text) or not text[index].isalnum()


def _contains_word(text: str, needle: str) -> bool:
    start = text.find(needle)
    while start != -1:
        if _boundary(text, start - 1) and _boundary(text, start + len(needle)):
            return True
        start = text.find(needle, start + 1)
    return False


def _strings(node: object) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)


def _texts(payload: bytes | str) -> list[str] | None:
    """Texts to inspect: the decoded JSON strings, or the raw text. None means: block."""
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        # Numbers come back as their literal text, so a phone sent as a number is seen too.
        return list(_strings(json.loads(payload, parse_int=str, parse_float=str)))
    except RecursionError:
        return None  # too deep to inspect: it could hide an escaped value
    except ValueError:
        return [payload]  # not JSON: the raw text is checked


def _found(texts: list[str], vault: Vault) -> bool:
    normals = [_normal(text) for text in texts]
    compacts = [_compact(text) for text in normals]
    # _hidden_values is package-internal API: only mask/, restore/ and guard/ may call it.
    for _, value in vault._hidden_values():
        needle = _normal(value)
        folded = _compact(needle)
        if len(folded) >= _MIN_COMPACT:
            if any(folded in text for text in compacts):
                return True
        elif needle and any(_contains_word(text, needle) for text in normals):
            return True
    return False


def check(payload: bytes | str, vault: Vault) -> None:
    """Raise EgressBlocked if any hidden value of this request is in the payload."""
    if not len(vault):
        return
    texts = _texts(payload)
    # Raised outside any except block: no decode error or value becomes the __context__.
    if texts is None or _found(texts, vault):
        raise EgressBlocked()


__all__ = ["check"]
