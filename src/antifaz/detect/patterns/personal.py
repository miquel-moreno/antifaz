"""Patterns for personal data without a check digit, plus cards and digit-only EU ids.

Email, IP, phone, card, passport, plate, address, date of birth and the Portuguese,
French and German identifiers that are only digits (these need a keyword before them).
Every repetition is bounded and separators are single characters that cannot be confused
with the parts they separate, so matching stays linear (ADR-0008). Candidates that can
run into the next value (cards) are searched by hand, like IBANs in identifiers.py.
"""

import re
from collections.abc import Callable, Iterator
from datetime import date

from stdnum.de import idnr
from stdnum.fr import nir
from stdnum.pt import nif

from antifaz.detect.patterns.context import has_context
from antifaz.detect.types import Confidence, EntityType, Layer, Span
from antifaz.detect.validators import luhn

_ASCII_CI = re.IGNORECASE | re.ASCII
_START = r"(?<![0-9A-Za-z])"
_END = r"(?![0-9A-Za-z])"
_DIGIT_START = r"(?<![0-9A-Za-z])(?<![0-9][ .-])"  # not glued to a longer number
_DIGIT_END = r"(?![0-9A-Za-z])(?![ .-][0-9])"


def _keywords(words: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive keywords (Unicode, so "TELÉFONO" matches "teléfono")."""
    return re.compile(rf"(?<!\w)(?:{words})(?!\w)", re.IGNORECASE)


def _span(match: re.Match[str], entity_type: EntityType, layer: Layer, conf: Confidence) -> Span:
    return Span(match.start(), match.end(), entity_type, layer, conf)


# --- Email ------------------------------------------------------------------------------

_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}+@"
    # Not possessive: the last label must be able to give back a sentence-ending period.
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.){1,8}[A-Za-z]{2,24}"
    r"(?![A-Za-z0-9-])",
    re.ASCII,
)


def _emails(text: str) -> Iterator[Span]:
    for match in _EMAIL.finditer(text):
        yield _span(match, EntityType.EMAIL, Layer.PATTERN, Confidence.HIGH)


# --- IPv4 --------------------------------------------------------------------------------

_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
_IP = re.compile(rf"(?<![0-9A-Za-z.]){_OCTET}(?:\.{_OCTET}){{3}}(?![0-9A-Za-z]|\.[0-9])")


def _ips(text: str) -> Iterator[Span]:
    for match in _IP.finditer(text):
        yield _span(match, EntityType.IP, Layer.PATTERN, Confidence.MEDIUM)


# --- Phone (Spain) -------------------------------------------------------------------------

# Business numbers (800, 900, 901, 902, 905) and premium rate (803, 806, 807; CNMC).
_BUSINESS_PREFIXES = frozenset({"800", "803", "806", "807", "900", "901", "902", "905"})
_PHONE = re.compile(
    rf"{_DIGIT_START}(?<!\+)(?:(?:\+34|0034|\(\+34\))[ .-]?)?[6789](?:[ .-]?[0-9]){{8}}{_DIGIT_END}"
)
_PHONE_KEYWORDS = _keywords(r"tel|tel[eé]fono|m[oó]vil|llamar?|whatsapp")


def _phones(text: str) -> Iterator[Span]:
    for match in _PHONE.finditer(text):
        national = re.sub(r"[^0-9]", "", match.group())[-9:]
        if national[:3] in _BUSINESS_PREFIXES:
            continue
        context = has_context(text, match.start(), _PHONE_KEYWORDS)
        confidence = Confidence.HIGH if context else Confidence.MEDIUM
        yield _span(match, EntityType.PHONE, Layer.PATTERN, confidence)


# --- Payment card --------------------------------------------------------------------------

_CARD_CANDIDATE = re.compile(rf"{_START}[0-9](?:[ -]?[0-9]){{12,18}}+")


def is_card_prefix(digits: str) -> bool:
    """Visa 4; Mastercard 51-55 and 2221-2720; Amex 34, 37; Discover 6011, 65, 644-649."""
    first_two, first_four = int(digits[:2]), int(digits[:4])
    return (
        digits[0] == "4"
        or 51 <= first_two <= 55
        or 2221 <= first_four <= 2720
        or first_two in (34, 37, 65)
        or first_four == 6011
        or 644 <= int(digits[:3]) <= 649
    )


def _card_at(text: str, match: re.Match[str]) -> Span | None:
    """The longest valid card (13-19 digits) that starts where the candidate starts."""
    ends = [match.start() + i + 1 for i, char in enumerate(match.group()) if char.isdigit()]
    digits = "".join(char for char in match.group() if char.isdigit())
    for length in range(min(19, len(ends)), 12, -1):
        end = ends[length - 1]
        if end < len(text) and text[end].isascii() and text[end].isalnum():
            continue
        if is_card_prefix(digits) and luhn.is_valid(digits[:length]):
            return Span(
                match.start(), end, EntityType.CREDIT_CARD, Layer.VALIDATOR, Confidence.HIGH
            )
    return None


def _cards(text: str) -> Iterator[Span]:
    # By hand, not with finditer: a candidate can run into the next card.
    position = 0
    while match := _CARD_CANDIDATE.search(text, position):
        span = _card_at(text, match)
        position = span.end if span else match.start() + 1
        if span:
            yield span


# --- Values that need a keyword before them -----------------------------------------------

_PASSPORT = re.compile(rf"{_START}[A-Z]{{3}}[0-9]{{6}}{_END}", _ASCII_CI)
_PASSPORT_KEYWORDS = _keywords(r"pasaporte|passport|pasap")

_PLATE = re.compile(
    rf"{_START}[0-9]{{4}}[ -]?[BCDFGHJKLMNPRSTVWXYZ]{{3}}(?![0-9A-Za-zÑñ])", _ASCII_CI
)
_PLATE_KEYWORDS = _keywords(r"matr[ií]cula|placa|coche|veh[ií]culo|moto")

_MONTHS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]  # fmt: skip
_NUMERIC_DATE = re.compile(r"(?<![0-9])([0-9]{1,2})([/.-])([0-9]{1,2})\2([0-9]{4})(?![0-9])")
_WORD_DATE = re.compile(
    rf"(?<![0-9])([0-9]{{1,2}}) de ({'|'.join(_MONTHS)}) de ([0-9]{{4}})(?![0-9])", re.IGNORECASE
)
_BIRTH_KEYWORDS = _keywords(r"nacid[oa]|nacimiento|f\. ?nac|fecha de nac|born|dob")


def _is_date(day: str, month: str, year: str) -> bool:
    try:
        date(int(year), int(month), int(day))
    except ValueError:
        return False
    return True


def _with_keyword(
    text: str,
    pattern: re.Pattern[str],
    keywords: re.Pattern[str],
    entity_type: EntityType,
    accept: Callable[[re.Match[str]], bool] = lambda _: True,
    layer: Layer = Layer.PATTERN,
    confidence: Confidence = Confidence.MEDIUM,
) -> Iterator[Span]:
    for match in pattern.finditer(text):
        if accept(match) and has_context(text, match.start(), keywords):
            yield _span(match, entity_type, layer, confidence)


def _birth_dates(text: str) -> Iterator[Span]:
    yield from _with_keyword(
        text,
        _NUMERIC_DATE,
        _BIRTH_KEYWORDS,
        EntityType.DATE_OF_BIRTH,
        lambda m: _is_date(m[1], m[3], m[4]),
    )
    yield from _with_keyword(
        text,
        _WORD_DATE,
        _BIRTH_KEYWORDS,
        EntityType.DATE_OF_BIRTH,
        lambda m: _is_date(m[1], str(_MONTHS.index(m[2].lower()) + 1), m[3]),
    )


# Digit-only EU identifiers: a 9-digit Portuguese NIF looks like a Spanish phone, so they
# are only reported after a keyword of their own country (examples: python-stdnum docs).
_EU_DIGIT_IDS: tuple[
    tuple[EntityType, re.Pattern[str], re.Pattern[str], Callable[[str], bool]], ...
] = (
    (
        EntityType.PT_NIF,
        re.compile(rf"{_START}[0-9]{{9}}{_END}"),
        _keywords(r"contribuinte|NIF PT|Portugal"),
        nif.is_valid,
    ),
    (
        EntityType.FR_NIR,
        re.compile(rf"{_START}[0-9](?: ?[0-9]){{14}}{_END}"),
        _keywords(r"NIR|s[ée]curit[ée] sociale|INSEE"),
        nir.is_valid,
    ),
    (
        EntityType.DE_IDNR,
        re.compile(rf"{_START}[0-9](?: ?[0-9]){{10}}{_END}"),
        _keywords(r"Steuer-ID|Steueridentifikationsnummer|IdNr|Identifikationsnummer"),
        idnr.is_valid,
    ),
)


def _eu_digit_ids(text: str) -> Iterator[Span]:
    for entity_type, pattern, keywords, is_valid in _EU_DIGIT_IDS:
        yield from _with_keyword(
            text,
            pattern,
            keywords,
            entity_type,
            _validated_by(is_valid),
            Layer.VALIDATOR,
            Confidence.HIGH,
        )


def _validated_by(is_valid: Callable[[str], bool]) -> Callable[[re.Match[str]], bool]:
    def accept(match: re.Match[str]) -> bool:
        try:
            return bool(is_valid(match.group()))
        except Exception:  # stdnum should not raise here; never let a value escape in an error
            return False

    return accept


# --- Spanish-style address -------------------------------------------------------------------

_STREET_TYPE = (
    r"(?i:C/|Calle|Avda\.|Avenida|Av\.|Pl\.|Plaza|Paseo|Pº|Ctra\.|Carretera|Camino|Ronda"
    r"|Traves[íi]a)"
)
_NAME_WORD = r"[A-ZÁÉÍÓÚÑÜÇÀÈÒÏ][^\W\d_]{0,30}"
_CONNECTOR = r"(?:de|del|la|las|los|el|y|i|d')"
_NAME = rf"(?:{_CONNECTOR} ){{0,3}}{_NAME_WORD}(?: (?:{_CONNECTOR} ){{0,3}}{_NAME_WORD}){{0,5}}"
_POSTCODE = r"(?:0[1-9]|[1-4][0-9]|5[0-2])[0-9]{3}(?![0-9])"
_ADDRESS = re.compile(
    rf"(?<![^\W_]){_STREET_TYPE} ?{_NAME},? (?:nº\.? ?)?[0-9]{{1,4}}[A-Za-z]?(?![^\W_])"
    rf"(?:,? [0-9]{{1,2}}[ºª](?: ?(?:[A-Z]|[0-9]{{1,2}}[ºª]?)(?![^\W_]))?)?"
    rf"(?P<postcode>,? {_POSTCODE})?"
)


def _addresses(text: str) -> Iterator[Span]:
    for match in _ADDRESS.finditer(text):
        confidence = Confidence.HIGH if match["postcode"] else Confidence.MEDIUM
        yield _span(match, EntityType.ADDRESS, Layer.PATTERN, confidence)


def find_patterns(text: str) -> list[Span]:
    """Candidate spans of every pattern-based entity in the text (not yet resolved)."""
    return [
        *_emails(text),
        *_ips(text),
        *_phones(text),
        *_cards(text),
        *_with_keyword(text, _PASSPORT, _PASSPORT_KEYWORDS, EntityType.ES_PASSPORT),
        *_with_keyword(text, _PLATE, _PLATE_KEYWORDS, EntityType.ES_PLATE),
        *_birth_dates(text),
        *_eu_digit_ids(text),
        *_addresses(text),
    ]
