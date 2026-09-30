"""Restorer: puts the original values back in the answer (plain text in this version).

One single pass (ADR-0012): an escape `[[...!` loses its `!`; a placeholder is replaced
only if it was emitted in this same request, otherwise it is left as it is. Restored
values are never scanned again. Spaces and lower case inside `[[ ... ]]` are tolerated.

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


__all__ = ["placeholder_tokens", "restore"]
