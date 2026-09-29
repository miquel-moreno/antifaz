"""Egress guard: a second check on the final bytes before they leave for a provider.

Looks for any value the policy said to hide, in the raw bytes and in decoded JSON
strings (values and keys, so `\\u` escapes are covered), with normalisation (NFC and
casefold; identifiers and phones also without spaces, dots, hyphens and slashes) and
alphanumeric boundaries, so "Ana" does not match inside "semana". If it finds one, the
request is blocked. A false positive also blocks: the guard fails closed on purpose.

Plain `str.find` loops, no regular expressions (ADR-0008).

Forbidden: It cannot be disabled.
"""

import json
import unicodedata
from collections.abc import Iterator

from antifaz.detect.types import EntityType
from antifaz.detect.validators._ascii import compact
from antifaz.errors import EgressBlocked
from antifaz.vault import Vault

_SEPARATORS = frozenset(" .-/")
# Types whose value keeps its meaning without separators. Emails, IPs and addresses do
# not: their dots or spaces are part of the value, and dropping them would match noise.
_NOT_COMPACTED = frozenset({EntityType.EMAIL, EntityType.IP, EntityType.ADDRESS})
_MIN_COMPACT = 6  # a shorter compact form would match unrelated digits


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _boundary(text: str, index: int) -> bool:
    """True when there is no letter or digit at text[index] (or it is outside the text)."""
    return not 0 <= index < len(text) or not text[index].isalnum()


def _contains(text: str, needle: str) -> bool:
    start = text.find(needle)
    while start != -1:
        if _boundary(text, start - 1) and _boundary(text, start + len(needle)):
            return True
        start = text.find(needle, start + 1)
    return False


def _contains_compact(text: str, needle: str) -> bool:
    """Search without separators; boundaries are checked in the original text."""
    kept = [i for i, char in enumerate(text) if char not in _SEPARATORS]
    stripped = "".join(text[i] for i in kept)
    start = stripped.find(needle)
    while start != -1:
        first, last = kept[start], kept[start + len(needle) - 1]
        if _boundary(text, first - 1) and _boundary(text, last + 1):
            return True
        start = stripped.find(needle, start + 1)
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
    """Raw text plus every JSON string, normalised. None means: block."""
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
    texts = [payload]
    try:
        texts.extend(_strings(json.loads(payload)))
    except RecursionError:
        return None  # too deep to inspect: it could hide an escaped value
    except ValueError:
        pass  # not JSON: the raw text is checked
    return [_norm(text) for text in texts]


def _found(texts: list[str], vault: Vault) -> bool:
    # _hidden_values is package-internal API: only mask/, restore/ and guard/ may call it.
    for entity, value in vault._hidden_values():
        needle = _norm(value)
        folded = _norm(compact(value)) if entity not in _NOT_COMPACTED else ""
        for text in texts:
            if needle and _contains(text, needle):
                return True
            if len(folded) >= _MIN_COMPACT and _contains_compact(text, folded):
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
