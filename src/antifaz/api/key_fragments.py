"""Recognisable pieces of a configured key (invariant 13, issue 76).

A provider that rejects its key may quote part of it in the error: the generic prefix and the
last characters behind stars or dots (`sk-proj-****...WxYz`). Invariant 13 covers those pieces,
not only the whole key. KeyFragments finds, for every configured key:

- the whole key;
- any run of FRAGMENT_LENGTH (10) consecutive characters of its SECRET PART: the key without its
  generic, public prefix (`sk-`, `sk-proj-`, `sk-ant-api03-`...). The prefix alone is the same
  for every key of a provider and is never a fragment. Ten characters are at least 40 bits even
  for a hexadecimal key (about 59 for the usual letters and digits), so ordinary text does not
  match one by chance, and a shorter piece is too little of the key to be useful;
- the provider-style masked key, with SHOWN_SUFFIX (4) to 9 characters of the secret part
  visible at one end:
  - its last 4-9 characters right after a MASK (two or more of `*`, `•`, `·`, `.`, `…`, or one
    `…`) and followed by nothing that continues it (no letter, digit, `*` or `•`):
    `sk-proj-****...WxYz`;
  - its first 4-9 characters followed by a mask, with nothing before them that continues them
    (no letter, digit or mask character; the generic prefix ends in `-`): `sk-proj-AbCd****`.
  So `**WxYz**` (markdown bold) and "ends in WxYz" are not fragments.
A secret part shorter than FRAGMENT_LENGTH has no runs (only the whole key); one shorter than
2 * SHOWN_SUFFIX has no masked shape.

Fast, for every streamed event: the runs are a set, and the text is probed every
ANCHOR (5) characters for a 5-character piece of a secret part. Any 10-character run in the text
covers one of those probes whole, so the probes miss nothing; only a probe that hits is
confirmed against the runs around it. The masked shapes look for the 4 last and the 4 first
characters with str.find and check the few characters around. No regular expression: a long run
of dots or stars stays linear.

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


ANCHOR = FRAGMENT_LENGTH // 2  # any run of 10 holds a whole 5-character piece at a multiple of 5
_CONTINUES_AFTER = frozenset("*•")


def _masked_before(text: str, position: int) -> bool:
    """A mask ends right before `position`: two mask characters, or one `…`."""
    if position < 1 or text[position - 1] not in _MASK:
        return False
    return text[position - 1] == _ELLIPSIS or (position >= 2 and text[position - 2] in _MASK)


def _masked_after(text: str, end: int) -> bool:
    """A mask starts at `end`: two mask characters, or one `…`."""
    if end >= len(text) or text[end] not in _MASK:
        return False
    return text[end] == _ELLIPSIS or (end + 1 < len(text) and text[end + 1] in _MASK)


def _free_after(text: str, end: int) -> bool:
    return end >= len(text) or not (text[end].isalnum() or text[end] in _CONTINUES_AFTER)


def _free_before(text: str, position: int) -> bool:
    return position == 0 or not (text[position - 1].isalnum() or text[position - 1] in _MASK)


def _occurrences(text: str, piece: str) -> list[int]:
    found: list[int] = []
    position = text.find(piece)
    while position != -1:
        found.append(position)
        position = text.find(piece, position + 1)
    return found


class KeyFragments:
    """The fragments of `keys` (empty values ignored), searched in a text. Build it once per
    answer or stream: building costs about as much as one search."""

    def __init__(self, keys: Sequence[str]) -> None:
        self._keys = tuple(dict.fromkeys(key for key in keys if key))
        runs: set[str] = set()
        anchors: set[str] = set()
        # (secret part, its first 4, its last 4) for the secret parts long enough to be masked
        self._ends: list[tuple[str, str, str]] = []
        for key in self._keys:
            secret = secret_part(key)
            if len(secret) >= FRAGMENT_LENGTH:
                starts = range(len(secret) - FRAGMENT_LENGTH + 1)
                runs.update(secret[i : i + FRAGMENT_LENGTH] for i in starts)
                anchors.update(secret[i : i + ANCHOR] for i in range(len(secret) - ANCHOR + 1))
            if len(secret) >= 2 * SHOWN_SUFFIX:
                self._ends.append((secret, secret[:SHOWN_SUFFIX], secret[-SHOWN_SUFFIX:]))
        self._runs = frozenset(runs)
        self._anchors = frozenset(anchors)

    def found_in(self, text: str) -> bool:
        """True if `text` holds a key, a run of a secret part or a masked key's shape."""
        if any(key in text for key in self._keys):
            return True
        if self._runs and self._has_run(text):
            return True
        # A plain `in` first: almost no text holds the first or last 4 characters at all.
        return any(
            (last in text and _masked_tail(text, secret, last))
            or (first in text and _masked_head(text, secret, first))
            for secret, first, last in self._ends
        )

    def _has_run(self, text: str) -> bool:
        anchors, runs = self._anchors, self._runs
        for probe in range(0, len(text) - ANCHOR + 1, ANCHOR):
            if text[probe : probe + ANCHOR] in anchors:
                # Every run that covers this probe whole starts between probe - 5 and probe.
                for start in range(max(0, probe - ANCHOR), probe + 1):
                    if text[start : start + FRAGMENT_LENGTH] in runs:
                        return True
        return False


def _masked_tail(text: str, secret: str, last: str) -> bool:
    """`****...WxYz`: a mask, then the last 4 to 9 characters, then nothing that continues."""
    limit = min(FRAGMENT_LENGTH, len(secret))
    for position in _occurrences(text, last):
        end = position + SHOWN_SUFFIX
        if _free_after(text, end) and any(
            _masked_before(text, end - visible)
            and text.startswith(secret[-visible:], end - visible)
            for visible in range(SHOWN_SUFFIX, limit)
        ):
            return True
    return False


def _masked_head(text: str, secret: str, first: str) -> bool:
    """`AbCd****`: nothing that continues before, the first 4 to 9 characters, then a mask."""
    limit = min(FRAGMENT_LENGTH, len(secret))
    for position in _occurrences(text, first):
        if _free_before(text, position) and any(
            _masked_after(text, position + visible) and text.startswith(secret[:visible], position)
            for visible in range(SHOWN_SUFFIX, limit)
        ):
            return True
    return False


__all__ = ["ANCHOR", "FRAGMENT_LENGTH", "SHOWN_SUFFIX", "KeyFragments", "secret_part"]
