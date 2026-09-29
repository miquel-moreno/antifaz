"""Masker: replaces detected values with placeholders `[[TYPE_N]]` in the given texts.

Escapes any `[[` the user already wrote: a `!` goes after every run of two or more `[`
(ADR-0012), so `restore(mask(x)) == x` for any text.

Forbidden: Never touches non-text fields or reasoning (thinking) blocks returned by the model.
Never returns clear text when the detector fails.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from antifaz.detect.scan import scan
from antifaz.detect.types import EntityType, Span
from antifaz.errors import DetectorFailed
from antifaz.policy import DEFAULT_POLICY, Action, Policy
from antifaz.vault import Vault

Detector = Callable[[str], Sequence[Span]]

_BRACKET_RUN = re.compile(r"\[\[+")


def escape(text: str) -> str:
    """Put `!` after every maximal run of two or more `[` (ADR-0012)."""
    return _BRACKET_RUN.sub(lambda m: m.group(0) + "!", text)


@dataclass(frozen=True, slots=True)
class MaskResult:
    """Masked texts, in input order, and the table to restore the answer."""

    texts: tuple[str, ...]
    vault: Vault
    hidden: tuple[EntityType, ...]

    @property
    def text(self) -> str:
        if len(self.texts) != 1:
            raise ValueError("mask() got several texts: use .texts")
        return self.texts[0]


def _detect(text: str, detector: Detector) -> list[Span]:
    failed = False
    spans: list[Span] = []
    try:
        spans = list(detector(text))
    except Exception:  # any detector error blocks; the error itself may hold the text
        failed = True
    # Raised outside the except block so the original error is not even the __context__.
    if failed or not _well_formed(spans, len(text)):
        raise DetectorFailed() from None
    return spans


def _well_formed(spans: list[Span], length: int) -> bool:
    position = 0
    for span in spans:
        if (
            not isinstance(span, Span)
            # plain ints only: a float or bool would slice wrongly or fail later
            or type(span.start) is not int
            or type(span.end) is not int
            or not position <= span.start < span.end <= length
        ):
            return False
        position = span.end
    return True


def mask(
    texts: str | Sequence[str],
    policy: Policy = DEFAULT_POLICY,
    *,
    detector: Detector = scan,
) -> MaskResult:
    """Replace the personal data the policy hides by placeholders, numbered across all texts."""
    items = (texts,) if isinstance(texts, str) else tuple(texts)
    vault = Vault()
    hidden: dict[EntityType, None] = {}
    masked = []
    for text in items:
        out: list[str] = []
        clear_start = 0  # start of the pending run of text kept in clear
        for span in _detect(text, detector):
            if policy.action_for(span.type) is Action.ALLOW:
                continue
            out.append(escape(text[clear_start : span.start]))
            # _add is package-internal API: the Vault exposes no public way to read values.
            out.append(f"[[{vault._add(span.type, text[span.start : span.end])}]]")
            hidden.setdefault(span.type)
            clear_start = span.end
        out.append(escape(text[clear_start:]))
        masked.append("".join(out))
    return MaskResult(texts=tuple(masked), vault=vault, hidden=tuple(hidden))


__all__ = ["Detector", "MaskResult", "escape", "mask"]
