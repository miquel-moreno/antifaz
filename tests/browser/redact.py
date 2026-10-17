"""The redactor of the failure logs (PR 11c-2a). Standard library only, no Playwright.

First every known value (the gateway's tokens, the session cookie values and CSRF tokens the
tabs saw) is replaced as a literal; then any run of 32 or more URL-safe characters, whatever is
around it (`%3D<id>`, `\\u003d<id>`, JSON quotes): every token of the panel is one (hex of 64,
token_urlsafe(32) of 43), and ordinary log text has no run that long.
"""

import re
from collections.abc import Iterable

TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{32,}")
REDACTED = "<redacted>"


def redact(text: str, tokens: Iterable[str]) -> str:
    for token in sorted({token for token in tokens if token}, key=len, reverse=True):
        text = text.replace(token, REDACTED)
    return TOKEN_SHAPE.sub(REDACTED, text)
