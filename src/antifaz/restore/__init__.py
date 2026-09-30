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
from collections.abc import Callable

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

# Most characters held back. Only a run of spaces or tabs inside "[[ ... ]]" can make a
# possible placeholder of this request longer than this (the longest token is ~30
# characters): past it, the held text is let go as it is (documented limit, never an error).
MAX_HOLDBACK = 64
_BLANKS = " \t"
_TOKEN_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")


class StreamLimitExceeded(Exception):  # noqa: N818 - reads as the event it names
    """A streamed answer accumulated more than the gateway allows."""

    def __init__(self) -> None:
        super().__init__("streamed answer exceeded a size limit")  # fixed: never a value


def placeholder_tokens(text: str) -> frozenset[str]:
    """Upper-case tokens of the placeholders written in `text` (same pattern as restore)."""
    return frozenset(
        match.group("token").upper()
        for match in _ESCAPE_OR_PLACEHOLDER.finditer(text)
        if match.group("token") is not None
    )


def restore(text: str, vault: Vault, *, encode: Callable[[str], str] | None = None) -> str:
    """Undo the escapes and put back the values of this request's placeholders.

    `encode`, if given, is applied to each value put back (for example, JSON string escaping
    when the text is JSON that could not be parsed).
    """

    def replace(match: re.Match[str]) -> str:
        brackets = match.group("esc")
        if brackets is not None:
            return brackets
        # _lookup is package-internal API: only mask/, restore/ and guard/ may call it.
        value = vault._lookup(match.group("token").upper())
        if value is None:
            return match.group(0)
        return value if encode is None else encode(value)

    return _ESCAPE_OR_PLACEHOLDER.sub(replace, text)


def _could_be_ours(tail: str, tokens: frozenset[str]) -> bool:
    """False if the placeholder being written in `tail` ("[[ es_dni_1 ]") can no longer be
    one of this request's tokens: restore() would then leave it as it is anyway."""
    rest = tail[2:].lstrip(_BLANKS)
    size = 0
    while size < len(rest) and rest[size] in _TOKEN_CHARS:
        size += 1
    token = rest[:size].upper()
    if size == len(rest):  # still writing the token (or nothing yet)
        return any(known.startswith(token) for known in tokens)
    return token in tokens  # the token is finished: spaces or "]" come after it


def _safe_end(text: str, tokens: frozenset[str]) -> int:
    """Where `text` can be cut so that restore(head) never changes whatever comes next.

    Only a tail that restore() could still read as an escape or as a placeholder of this
    request is held: it starts in the last run of "[" (no other "[" can follow the start of
    one).
      - The text ends with "[" run: its brackets are text whatever follows (a "!" only drops
        the "!"; a placeholder only uses the last two), so only the last two are held.
      - "[[" + a placeholder being written ("[[ es_dni_1 ]") that can still become one of
        this request's tokens: held from that "[[". Any other token would be left as it is by
        restore(), so it is not held (a wiki link "[[Pagina]]" flows at once).
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
    if (
        start >= 0
        and _PLACEHOLDER_START.fullmatch(text, start)
        and _could_be_ours(text[start:], tokens)
    ):
        return start
    return len(text)


class StreamRestorer:
    """Restores one streamed text field. feed() each chunk in order, then flush() once.

    The concatenation of every feed() and the final flush() equals restore() of the whole
    text (invariant 3). Documented limit: a possible placeholder held for more than
    MAX_HOLDBACK characters (only possible with a long run of spaces or tabs inside the
    brackets) is let go as it is, so it shows as a placeholder, never as a wrong value.
    """

    __slots__ = ("_pending", "_tokens", "_vault")

    def __init__(self, vault: Vault) -> None:
        self._vault = vault
        # _tokens is package-internal API: placeholders of this request, never values.
        self._tokens = vault._tokens()
        self._pending = ""

    @property
    def pending(self) -> int:
        """Characters held back right now."""
        return len(self._pending)

    def feed(self, chunk: str) -> str:
        text = self._pending + chunk
        cut = _safe_end(text, self._tokens)
        if len(text) - cut > MAX_HOLDBACK:
            cut = len(text)  # the documented limit: let it go, never block the stream
        self._pending = text[cut:]
        return restore(text[:cut], self._vault)

    def flush(self) -> str:
        """The held back tail, restored (an unfinished placeholder stays as it is)."""
        text, self._pending = self._pending, ""
        return restore(text, self._vault)


__all__ = ["MAX_HOLDBACK", "StreamLimitExceeded", "StreamRestorer", "placeholder_tokens", "restore"]
