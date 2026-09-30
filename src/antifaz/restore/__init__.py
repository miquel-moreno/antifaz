"""Restorer: puts the original values back in the answer, whole or streamed.

One single pass (ADR-0012): an escape `[[...!` loses its `!`; a placeholder is replaced
only if it was emitted in this same request, otherwise it is left as it is. Restored
values are never scanned again. Spaces and lower case inside `[[ ... ]]` are tolerated.

Streaming (ADR-0013, invariant 3): `StreamRestorer` emits every chunk at once except a short
tail that could still become a placeholder or an escape with the SAME pattern as restore().
The result never depends on how the text is cut into chunks.

Forbidden: Never restore inside reasoning blocks or unknown events: those pass through untouched.
"""

import re

from antifaz.vault import Vault

_ESCAPE_OR_PLACEHOLDER = re.compile(
    r"(?P<esc>\[\[+)!|\[\[[ \t]*(?P<token>[A-Za-z]+(?:_[A-Za-z]+)*_[0-9]+)[ \t]*\]\]",
    # ASCII: without it, IGNORECASE lets look-alikes such as dotless i, long s or the Kelvin sign
    # match [A-Za-z], and .upper() would turn them into a real token.
    re.IGNORECASE | re.ASCII,
)
# Every text that the placeholder alternative above could still complete: "[[", spaces, the
# token being written, its digits, spaces and one "]". Same classes and flags as above.
_PLACEHOLDER_START = re.compile(
    r"\[\[[ \t]*(?:[A-Za-z]+(?:_[A-Za-z]+)*(?:_(?:[0-9]+[ \t]*\]?)?)?)?",
    re.IGNORECASE | re.ASCII,
)
# Longest tail held back: the longest placeholder (type name of 17 letters, "_" and a
# 9-digit number) with 16 spaces or tabs on each side fits. More than this is refused.
MAX_HOLDBACK = 64


class StreamLimitExceeded(Exception):  # noqa: N818 - reads as the event it names
    """A streamed answer held back or accumulated more than the gateway allows."""

    def __init__(self) -> None:
        super().__init__("streamed answer exceeded a size limit")  # fixed: never a value


def placeholder_tokens(text: str) -> frozenset[str]:
    """Upper-case tokens of the placeholders written in `text` (same pattern as restore)."""
    return frozenset(
        match.group("token").upper()
        for match in _ESCAPE_OR_PLACEHOLDER.finditer(text)
        if match.group("token") is not None
    )


def restore(text: str, vault: Vault) -> str:
    """Undo the escapes and put back the values of this request's placeholders."""

    def replace(match: re.Match[str]) -> str:
        brackets = match.group("esc")
        if brackets is not None:
            return brackets
        # _lookup is package-internal API: only mask/, restore/ and guard/ may call it.
        value = vault._lookup(match.group("token").upper())
        return match.group(0) if value is None else value

    return _ESCAPE_OR_PLACEHOLDER.sub(replace, text)


def _safe_end(text: str) -> int:
    """Where `text` can be cut so that restore(head) never changes whatever comes next.

    Only a tail that restore() could still read as an escape or a placeholder is held: it
    starts in the last run of "[" (no other "[" can follow the start of one).
      - The text ends with "[" run: its brackets are text whatever follows (a "!" only drops
        the "!"; a placeholder only uses the last two), so only the last two are held.
      - "[[" + a placeholder being written ("[[ es_dni_1 ]"): held from that "[[".
    Anything else is final and is cut at the end.
    """
    last = text.rfind("[")
    if last == -1:
        return len(text)
    if last == len(text) - 1:
        run_start = last
        while run_start > 0 and text[run_start - 1] == "[":
            run_start -= 1
        return max(run_start, len(text) - 2)
    start = last - 1
    if start >= 0 and _PLACEHOLDER_START.fullmatch(text, start):
        return start
    return len(text)


class StreamRestorer:
    """Restores one streamed text field. feed() each chunk in order, then flush() once.

    The concatenation of every feed() and the final flush() equals restore() of the whole
    text (invariant 3). Held back text is at most MAX_HOLDBACK characters: a longer possible
    placeholder raises StreamLimitExceeded (the held text stays for flush()).
    """

    __slots__ = ("_pending", "_vault")

    def __init__(self, vault: Vault) -> None:
        self._vault = vault
        self._pending = ""

    @property
    def pending(self) -> int:
        """Characters held back right now."""
        return len(self._pending)

    def feed(self, chunk: str) -> str:
        text = self._pending + chunk
        cut = _safe_end(text)
        if len(text) - cut > MAX_HOLDBACK:
            self._pending = text  # nothing is lost: flush() still gives it back restored
            raise StreamLimitExceeded()
        self._pending = text[cut:]
        return restore(text[:cut], self._vault)

    def flush(self) -> str:
        """The held back tail, restored (an unfinished placeholder stays as it is)."""
        text, self._pending = self._pending, ""
        return restore(text, self._vault)


__all__ = ["MAX_HOLDBACK", "StreamLimitExceeded", "StreamRestorer", "placeholder_tokens", "restore"]
