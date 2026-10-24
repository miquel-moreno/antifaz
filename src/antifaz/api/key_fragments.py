"""Recognisable pieces of a configured key (invariant 13, issue 76).

A provider that rejects its key may quote part of it in the error: the generic prefix and the
last characters behind stars or dots (`sk-proj-****...WxYz`). Invariant 13 covers those pieces,
not only the whole key. KeyFragments finds, for every configured key:

- any run of FRAGMENT_LENGTH (10) consecutive characters of its SECRET PART: the key without its
  generic, public prefix (`sk-`, `sk-proj-`, `sk-ant-api03-`...). The prefix alone is the same
  for every key of a provider and is never a fragment. Ten characters are at least 40 bits even
  for a hexadecimal key (about 59 for the usual letters and digits), so ordinary text never
  matches one by chance, and a shorter piece is too little of the key to be useful;
- the provider-style masked key: its last SHOWN_SUFFIX (4) characters right after a run of two
  or more of `*`, `•`, `·`, `.`, `…` (or one `…`), and not followed by a letter or digit. Four
  characters alone are not a fragment ("ends in WxYz" passes): only that shape is recognised.

The whole key is always found. A secret part shorter than FRAGMENT_LENGTH has no runs (only the
whole key); one shorter than 2 * SHOWN_SUFFIX has no masked shape.

Plain string search only, no regular expression: a long run of dots or stars stays linear.

Forbidden: logging or returning a key or a fragment. Caching fragments beyond the caller.
"""

from collections.abc import Sequence

FRAGMENT_LENGTH = 10
SHOWN_SUFFIX = 4

# The generic, public prefixes of provider keys, longest first where one starts another.
_FIXED_PREFIXES = ("sk-svcacct-", "sk-proj-", "sk-admin-", "sk-None-")
_ANTHROPIC = "sk-ant-"
_MASK = frozenset("*•·.…")
_ELLIPSIS = "…"


def _anthropic_prefix(key: str) -> str:
    """`sk-ant-` plus a kind and a version (`api03-`, `admin01-`, `oat01-`), if present."""
    rest = key[len(_ANTHROPIC) :]
    kind_end = rest.find("-")
    kind = rest[:kind_end] if kind_end > 0 else ""
    letters = kind.rstrip("0123456789")
    if kind and letters.isascii() and letters.isalpha() and letters.islower():
        return _ANTHROPIC + kind + "-"
    return _ANTHROPIC


def secret_part(key: str) -> str:
    """The key without its generic prefix: the part that differs from key to key."""
    if key.startswith(_ANTHROPIC):
        return key[len(_anthropic_prefix(key)) :]
    for prefix in _FIXED_PREFIXES:
        if key.startswith(prefix):
            return key[len(prefix) :]
    return key.removeprefix("sk-")


def _is_masked_before(text: str, position: int) -> bool:
    """True if a mask run ends right before `position`: two mask characters, or one `…`."""
    if position < 1 or text[position - 1] not in _MASK:
        return False
    if text[position - 1] == _ELLIPSIS:
        return True
    return position >= 2 and text[position - 2] in _MASK


def _ends_there(text: str, end: int) -> bool:
    return end >= len(text) or not text[end].isalnum()


class KeyFragments:
    """The fragments of `keys` (empty values ignored), searched in a text."""

    def __init__(self, keys: Sequence[str]) -> None:
        runs: set[str] = set()
        suffixes: set[str] = set()
        for key in keys:
            if not key:
                continue
            runs.add(key)
            secret = secret_part(key)
            runs.update(
                secret[start : start + FRAGMENT_LENGTH]
                for start in range(len(secret) - FRAGMENT_LENGTH + 1)
            )
            if len(secret) >= 2 * SHOWN_SUFFIX:
                suffixes.add(secret[-SHOWN_SUFFIX:])
        self._runs = tuple(runs)
        self._suffixes = tuple(suffixes)

    def found_in(self, text: str) -> bool:
        """True if `text` holds a key, a run of a secret part or a masked key's shape."""
        if any(run in text for run in self._runs):
            return True
        return any(self._masked_suffix(text, suffix) for suffix in self._suffixes)

    @staticmethod
    def _masked_suffix(text: str, suffix: str) -> bool:
        position = text.find(suffix)
        while position != -1:
            if _is_masked_before(text, position) and _ends_there(text, position + len(suffix)):
                return True
            position = text.find(suffix, position + 1)
        return False


__all__ = ["FRAGMENT_LENGTH", "SHOWN_SUFFIX", "KeyFragments", "secret_part"]
