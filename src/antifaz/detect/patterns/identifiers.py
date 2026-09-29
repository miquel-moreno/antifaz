"""Find identifiers with a check digit in free text.

Each pattern only proposes candidates; a span is reported only if its validator accepts
the value. Patterns follow ADR-0008: every repetition is bounded, and optional
separators are single characters that cannot be confused with a digit or a letter, so
matching stays linear. Lookarounds stop an identifier from being found inside a longer
alphanumeric run. Letters are matched in either case, ASCII only: without re.ASCII,
IGNORECASE would let some non-ASCII letters (the long s, the Kelvin sign) match S and K.
"""

import re
from collections.abc import Callable, Iterator

from antifaz.detect.types import Confidence, EntityType, Layer, Span
from antifaz.detect.validators import ccc, cif, dni, eu, iban, nie, nif_klm, nss

_FLAGS = re.IGNORECASE | re.ASCII
# Letters with accents (Latin-1 and Latin Extended-A/B) are word characters too: in
# "DNI és 16257107-V" the "s" of "és" must not start a value.
_START = r"(?<![0-9A-Za-z\u00C0-\u024F])"
_END = r"(?![0-9A-Za-z\u00C0-\u024F])"
_SEP = r"[ .-]?"  # between the parts of a DNI, NIE, NIF or CIF
_EU_VAT_PREFIXES = (
    "AT|BE|BG|CY|CZ|DE|DK|EE|EL|ES|FI|FR|HR|HU|IE|IT|LT|LU|LV|MT|NL|PL|PT|RO|SE|SI|SK|XI"
)
_CF_DIGIT = "[0-9LMNPQRSTUV]"  # Italian codice fiscale: digits may be replaced by letters


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(f"{_START}{pattern}{_END}", _FLAGS)


_PATTERNS: tuple[tuple[EntityType, re.Pattern[str], Callable[[str], bool]], ...] = (
    (
        EntityType.ES_DNI,
        _compile(rf"(?:[0-9]{{8}}|[0-9]{{2}}\.[0-9]{{3}}\.[0-9]{{3}}){_SEP}[A-Z]"),
        dni.is_valid,
    ),
    (
        EntityType.ES_NIE,
        _compile(rf"[XYZ]{_SEP}(?:[0-9]{{7}}|[0-9]\.[0-9]{{3}}\.[0-9]{{3}}){_SEP}[A-Z]"),
        nie.is_valid,
    ),
    (
        EntityType.ES_NIF,
        _compile(rf"[KLM]{_SEP}(?:[0-9]{{7}}|[0-9]\.[0-9]{{3}}\.[0-9]{{3}}){_SEP}[A-Z]"),
        nif_klm.is_valid,
    ),
    (
        EntityType.ES_CIF,
        _compile(
            rf"[ABCDEFGHJNPQRSUVW]{_SEP}(?:[0-9]{{7}}|[0-9]{{2}}\.[0-9]{{3}}\.[0-9]{{2}}){_SEP}[0-9A-J]"
        ),
        cif.is_valid,
    ),
    (
        EntityType.ES_NSS,
        _compile(r"[0-9]{2}[ /-]?[0-9]{8}[ /-]?[0-9]{2}"),
        nss.is_valid,
    ),
    # 20 digits with a single space or hyphen allowed between any two of them: covers
    # "0012 0345 03 0000067890", "0012 0345 0300 0006 7890" and mixed separators.
    (EntityType.ES_CCC, _compile(r"[0-9](?:[ -]?[0-9]){19}"), ccc.is_valid),
    (
        EntityType.IT_CODICE_FISCALE,
        _compile(rf"[A-Z]{{6}}{_CF_DIGIT}{{2}}[A-Z]{_CF_DIGIT}{{2}}[A-Z]{_CF_DIGIT}{{3}}[A-Z]"),
        eu.it_codice_fiscale_is_valid,
    ),
    (
        EntityType.EU_VAT,
        # With a space after the prefix it must be upper case ("FR 61954506077"): otherwise
        # "DNI es 12345678Z" would read as a Spanish VAT number and swallow the word "es".
        _compile(rf"(?:(?-i:{_EU_VAT_PREFIXES}) |(?:{_EU_VAT_PREFIXES}))[0-9A-Z]{{2,12}}+"),
        eu.eu_vat_is_valid,
    ),
)

# An IBAN may be split by single spaces or hyphens anywhere and its length depends on the
# country, so the pattern takes up to the longest IBAN and _iban_at() cuts it to size.
_IBAN_CANDIDATE = re.compile(rf"{_START}[A-Z]{{2}}[0-9]{{2}}(?:[ -]?[A-Z0-9]){{11,30}}+", _FLAGS)


def _ends_at_boundary(text: str, end: int) -> bool:
    return end == len(text) or not text[end].isascii() or not text[end].isalnum()


def _iban_at(text: str, match: re.Match[str]) -> Span | None:
    """The valid IBAN that starts where the candidate starts, if any."""
    length = iban.length(match.group()[:2].upper())
    if length is None:  # unknown country (every registry country has a fixed length)
        return None
    # End offset in the text after each alphanumeric character of the candidate.
    ends = [match.start() + i + 1 for i, char in enumerate(match.group()) if char.isalnum()]
    if length > len(ends):
        return None
    end = ends[length - 1]
    if _ends_at_boundary(text, end) and iban.is_valid(text[match.start() : end]):
        return Span(match.start(), end, EntityType.IBAN, Layer.VALIDATOR, Confidence.HIGH)
    return None


def _iban_spans(text: str) -> Iterator[Span]:
    # Searched by hand, not with finditer: a candidate can run into the next IBAN, so after
    # a failed candidate the search goes on from the next character, not from its end.
    position = 0
    while match := _IBAN_CANDIDATE.search(text, position):
        span = _iban_at(text, match)
        if span is not None:
            yield span
            position = span.end
        else:
            position = match.start() + 1


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
