"""What the NER model reads: the detector's view with non-language runs turned into spaces.

A model reads subword pieces, and text that is not language (base64, hashes, long URLs) costs
many pieces for nothing: a 4 KB base64 blob took ~12 s of CPU, enough to pass the time limit,
get the worker killed and block every request while a new one loads. And a name glued to a
long number ("1234567890Jordi") is one word for the model, so it is not seen as a name.

So, before the windows are cut, the NER gets a copy of the text where these runs are spaces of
the same length: offsets do not move, spans go back unchanged, and validators and patterns
still read the full text (an IBAN, a DNI or an email inside the run is found there).

- URL-like runs (`scheme://...` or `www....`) of BLOB_CHARS or more.
- Runs of base64, base64url or hex characters without spaces of BLOB_CHARS or more that have
  a digit or a `+`, `/` or `=` (any such run of HUGE_RUN_CHARS or more): no word or name looks
  like that. Commas and semicolons break a run, so CSV rows are kept.
- Runs of GLUED_DIGITS or more digits touching a letter: the digits become spaces.

Nothing to backtrack (ADR-0008): character classes with a minimum length, no nesting.
"""

import re

BLOB_CHARS = 32
HUGE_RUN_CHARS = 64
GLUED_DIGITS = 6
# Part of the NER cache key: change it whenever the view changes.
VIEW_VERSION = f"view1:{BLOB_CHARS}:{HUGE_RUN_CHARS}:{GLUED_DIGITS}"

_RUN = re.compile(
    # The scheme has a bounded length: an unbounded one would retry a long run of letters
    # from every position (quadratic time).
    rf"(?:[A-Za-z][A-Za-z0-9+.-]{{0,15}}://|www\.)\S+|[A-Za-z0-9+/=_-]{{{BLOB_CHARS},}}"
)
_BLOB_SIGN = re.compile(r"[0-9+/=]")
# Only from the start of a digit run, taken whole (possessive): no retry from every digit.
_GLUED = re.compile(
    rf"(?<!\d)\d{{{GLUED_DIGITS},}}+(?=[^\W\d_])|(?<=[^\W\d_])\d{{{GLUED_DIGITS},}}"
)


def _blank_run(match: re.Match[str]) -> str:
    run = match.group()
    url = "://" in run[:40] or run.startswith("www.")
    if len(run) < BLOB_CHARS:
        return run
    if url or len(run) >= HUGE_RUN_CHARS or _BLOB_SIGN.search(run):
        return " " * len(run)
    return run


def ner_view(text: str) -> str:
    """`text` with blobs, long URLs and long digit runs glued to letters as spaces."""
    text = _RUN.sub(_blank_run, text)
    return _GLUED.sub(lambda match: " " * len(match.group()), text)
