"""Find identifiers with a check digit in free text.

Each pattern only proposes candidates; a span is reported only if its validator accepts
the value. Patterns follow ADR-0008: every repetition is bounded, the variable parts use
possessive quantifiers (no backtracking), and the lookarounds stop an identifier from
being found inside a longer alphanumeric run.
"""

import re
from collections.abc import Callable, Iterator

from antifaz.detect.types import Confidence, EntityType, Layer, Span
from antifaz.detect.validators import ccc, cif, dni, eu, iban, nie, nif_klm, nss

_START = r"(?<![0-9A-Za-z])"
_END = r"(?![0-9A-Za-z])"
_EU_VAT_PREFIXES = (
    "AT|BE|BG|CY|CZ|DE|DK|EE|EL|ES|FI|FR|HR|HU|IE|IT|LT|LU|LV|MT|NL|PL|PT|RO|SE|SI|SK|XI"
)
_CF_DIGIT = "[0-9LMNPQRSTUV]"  # Italian codice fiscale: digits may be replaced by letters

_PATTERNS: tuple[tuple[EntityType, re.Pattern[str], Callable[[str], bool]], ...] = (
    (
        EntityType.ES_DNI,
        re.compile(rf"{_START}(?:[0-9]{{8}}|[0-9]{{2}}\.[0-9]{{3}}\.[0-9]{{3}})-?[A-Za-z]{_END}"),
        dni.is_valid,
    ),
    (EntityType.ES_NIE, re.compile(rf"{_START}[XYZxyz]-?[0-9]{{7}}-?[A-Za-z]{_END}"), nie.is_valid),
    (
        EntityType.ES_NIF,
        re.compile(rf"{_START}[KLMklm]-?[0-9]{{7}}-?[A-Za-z]{_END}"),
        nif_klm.is_valid,
    ),
    (
        EntityType.ES_CIF,
        re.compile(rf"{_START}[ABCDEFGHJNPQRSUVW]-?[0-9]{{7}}-?[0-9A-J]{_END}"),
        cif.is_valid,
    ),
    (
        EntityType.ES_NSS,
        re.compile(rf"{_START}[0-9]{{2}}([ /-]?)[0-9]{{8}}\1[0-9]{{2}}{_END}"),
        nss.is_valid,
    ),
    (
        EntityType.ES_CCC,
        re.compile(rf"{_START}[0-9]{{4}}([ -]?)[0-9]{{4}}\1[0-9]{{2}}\1[0-9]{{10}}{_END}"),
        ccc.is_valid,
    ),
    (
        EntityType.IT_CODICE_FISCALE,
        re.compile(
            rf"{_START}[A-Z]{{6}}{_CF_DIGIT}{{2}}[A-Z]{_CF_DIGIT}{{2}}[A-Z]{_CF_DIGIT}{{3}}[A-Z]{_END}"
        ),
        eu.it_codice_fiscale_is_valid,
    ),
    (
        EntityType.EU_VAT,
        re.compile(rf"{_START}(?:{_EU_VAT_PREFIXES})[0-9A-Z]{{2,12}}+{_END}"),
        eu.eu_vat_is_valid,
    ),
)

# An IBAN may be split by single spaces anywhere; its length depends on the country, so the
# pattern takes up to the longest possible IBAN and _iban_spans() cuts it to size.
_IBAN_CANDIDATE = re.compile(rf"{_START}[A-Z]{{2}}[0-9]{{2}}(?: ?[A-Z0-9]){{11,30}}+")
_IBAN_MIN_LENGTH = 15


def _ends_at_boundary(text: str, end: int) -> bool:
    return end == len(text) or not text[end].isascii() or not text[end].isalnum()


def _iban_spans(text: str) -> Iterator[Span]:
    for match in _IBAN_CANDIDATE.finditer(text):
        # End offset (in the text) after each alphanumeric character of the candidate.
        ends = [match.start() + i + 1 for i, c in enumerate(match.group()) if c != " "]
        expected = iban.length(match.group()[:2])
        lengths = [expected] if expected else range(len(ends), _IBAN_MIN_LENGTH - 1, -1)
        for length in lengths:
            if length > len(ends):
                continue
            end = ends[length - 1]
            if _ends_at_boundary(text, end) and iban.is_valid(text[match.start() : end]):
                yield Span(match.start(), end, EntityType.IBAN, Layer.VALIDATOR, Confidence.HIGH)
                break


def find_identifiers(text: str) -> list[Span]:
    """Spans of every identifier in the text whose check digits are valid."""
    spans = [
        Span(match.start(), match.end(), entity_type, Layer.VALIDATOR, Confidence.HIGH)
        for entity_type, pattern, is_valid in _PATTERNS
        for match in pattern.finditer(text)
        if is_valid(match.group())
    ]
    spans.extend(_iban_spans(text))
    return spans
