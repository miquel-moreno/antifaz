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


def _keywords(words: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive keywords (Unicode, so "TELÉFONO" matches "teléfono")."""
    return re.compile(rf"(?<!\w)(?:{words})(?!\w)", re.IGNORECASE)


def _span(match: re.Match[str], entity_type: EntityType, layer: Layer, conf: Confidence) -> Span:
    return Span(match.start(), match.end(), entity_type, layer, conf)


# --- Email ------------------------------------------------------------------------------

# Local part: the RFC 5322 "atext" characters plus Unicode letters (RFC 6531), so that
# "Juan.Pérez@..." or "o'brien@..." are masked whole, not left half in clear.
_LOCAL_CHAR = r"[\w.!#$%&'*+/=?^`{|}~-]"
_EMAIL = re.compile(
    rf"(?<!{_LOCAL_CHAR}){_LOCAL_CHAR}{{1,64}}+@"
    # Not possessive: the last label must be able to give back a sentence-ending period.
    r"(?:[^\W_](?:[\w-]{0,61}[^\W_])?\.){1,8}(?:[^\W\d_]{2,24}|xn--[a-z0-9-]{1,59})"
    r"(?![\w-])",
    re.IGNORECASE,
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
# A run of digit groups with single separators, optionally after an international prefix.
# Phones are then assembled group by group (_phones_in_run), so a phone next to another
# number ("612345678 698765432", "CP 08001 612345678") is still found.
_PHONE_RUN = re.compile(
    r"(?<![0-9A-Za-z+])(?P<prefix>(?:\+ ?34|0034|\(\+34\))[ .-]?)?[0-9](?:[ ./-]?[0-9])*+"
)
_DIGIT_GROUP = re.compile(r"[0-9]+")
_PHONE_KEYWORDS = _keywords(r"tel|tel[eé]fono|m[oó]vil|llamar?|whatsapp")


def _phone_end(text: str, groups: list[tuple[int, int]], first: int) -> int | None:
    """Index after the last group of a 9-digit phone starting at groups[first], if any.

    The groups must be joined by the same separator ("600 123 456", "600.123.456").
    """
    total, separator = 0, None
    for i in range(first, len(groups)):
        if i > first:
            gap = text[groups[i - 1][1] : groups[i][0]]
            if separator is not None and gap != separator:
                return None
            separator = gap
        total += groups[i][1] - groups[i][0]
        if total == 9:
            return i + 1
        if total > 9:
            return None
    return None


def _phones_in_run(text: str, run: re.Match[str]) -> Iterator[Span]:
    body_start = run.start() + len(run["prefix"] or "")
    groups = [m.span() for m in _DIGIT_GROUP.finditer(text, body_start, run.end())]
    if run.end() < len(text) and text[run.end()].isascii() and text[run.end()].isalnum():
        groups.pop()  # glued to a letter ("600123456B"): not a phone
    i = 0
    while i < len(groups):
        start = groups[i][0]
        glued = text[groups[i][0] : groups[i][1]]
        if len(glued) == 11 and glued.startswith("34"):  # "34612345678" (e.g. wa.me links)
            after: int | None = i + 1
        else:
            after = _phone_end(text, groups, i)
        if after is None:
            i += 1
            continue
        end = groups[after - 1][1]
        national = re.sub(r"[^0-9]", "", text[start:end])[-9:]
        if national[0] in "6789" and national[:3] not in _BUSINESS_PREFIXES:
            if i == 0 and run["prefix"]:
                start = run.start()
            context = has_context(text, start, _PHONE_KEYWORDS)
            confidence = Confidence.HIGH if context else Confidence.MEDIUM
            yield Span(start, end, EntityType.PHONE, Layer.PATTERN, confidence)
            i = after
        else:
            i += 1


def _phones(text: str) -> Iterator[Span]:
    for run in _PHONE_RUN.finditer(text):
        yield from _phones_in_run(text, run)


# --- Payment card --------------------------------------------------------------------------

_CARD_CANDIDATE = re.compile(r"(?<![0-9A-Za-z])[0-9](?:[ .-]?[0-9]){12,18}+")
_CARD_NEXT = re.compile(r"[0-9](?:[ .-]?[0-9]){12,18}+")


def _inside_grouped_number(text: str, start: int) -> bool:
    """True if a group of 3+ digits comes right before `start`, with one separator.

    Then the candidate is probably the middle of a grouped number (e.g. the "345" of the
    phone "612 345 678"), not the start of a card. A lone 1-2 digit number (a count, an
    index: "serie 2 2223...") does not count.
    """
    if start < 2 or text[start - 1] not in " .-" or not text[start - 2].isdigit():
        return False
    digits = 0
    i = start - 2
    while i >= 0 and text[i].isdigit() and digits < 3:
        digits += 1
        i -= 1
    return digits >= 3


def is_card_prefix(digits: str) -> bool:
    """Visa 4; Mastercard 51-55, 2221-2720; Amex 34, 37; Discover 6011, 65, 644-649;
    JCB 3528-3589; Diners 300-305, 36, 38; UnionPay 62."""
    first_two, first_three, first_four = int(digits[:2]), int(digits[:3]), int(digits[:4])
    return (
        digits[0] == "4"
        or 51 <= first_two <= 55
        or 2221 <= first_four <= 2720
        or first_two in (34, 36, 37, 38, 62, 65)
        or first_four == 6011
        or 644 <= first_three <= 649
        or 3528 <= first_four <= 3589
        or 300 <= first_three <= 305
    )


def _card_at(text: str, match: re.Match[str]) -> Span | None:
    """The longest valid card (13-19 digits) that starts where the candidate starts."""
    ends = [match.start() + i + 1 for i, char in enumerate(match.group()) if char.isdigit()]
    digits = "".join(char for char in match.group() if char.isdigit())
    if not is_card_prefix(digits):
        return None
    for length in range(min(19, len(ends)), 12, -1):
        end = ends[length - 1]
        if end < len(text) and text[end].isascii() and text[end].isalnum():
            continue
        if luhn.is_valid(digits[:length]):
            return Span(
                match.start(), end, EntityType.CREDIT_CARD, Layer.VALIDATOR, Confidence.HIGH
            )
    return None


def _cards(text: str) -> Iterator[Span]:
    # By hand, not with finditer: a candidate can run into the next card.
    position = 0
    while match := _CARD_CANDIDATE.search(text, position):
        span = None if _inside_grouped_number(text, match.start()) else _card_at(text, match)
        if span is None:
            position = match.start() + 1
            continue
        while span is not None:  # cards one after the other, e.g. a list
            yield span
            position = span.end
            following = None
            if span.end < len(text) and text[span.end] in " .-":
                following = _CARD_NEXT.match(text, span.end + 1)
            span = _card_at(text, following) if following else None


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
# 12/03/1985, 12-03-1985, 12.03.1985, 12/03/85 · 1985-03-12 · 12 de marzo de 1985, 12 marzo
# 1985, 1 de marzo del 1985, 12 de marzo, 1985
_NUMERIC_DATE = re.compile(
    r"(?<![0-9])([0-9]{1,2})([/.-])([0-9]{1,2})\2([0-9]{4}|[0-9]{2})(?![0-9])"
)
_ISO_DATE = re.compile(r"(?<![0-9])([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})(?![0-9])")
_WORD_DATE = re.compile(
    rf"(?<![0-9])([0-9]{{1,2}}) (?:de )?({'|'.join(_MONTHS)})(?:,| de| del)? ([0-9]{{4}})(?![0-9])",
    re.IGNORECASE,
)
_BIRTH_KEYWORDS = _keywords(
    r"nacid[oa]|naci[oó]|nac[ií]|nacimiento|f\. ?nac|fecha de nac|fecha nac|fnac|fec\. ?nac"
    r"|born|dob|d\.o\.b\.?|date of birth"
)


def _is_date(day: str, month: str, year: str) -> bool:
    """A real calendar date; a 2-digit year is valid if it is in the 1900s or the 2000s."""
    years = [year] if len(year) == 4 else [f"19{year}", f"20{year}"]
    for full_year in years:
        try:
            date(int(full_year), int(month), int(day))
        except ValueError:
            continue
        return True
    return False


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
        _ISO_DATE,
        _BIRTH_KEYWORDS,
        EntityType.DATE_OF_BIRTH,
        lambda m: _is_date(m[3], m[2], m[1]),
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

# Street types that cannot be anything else: the name may be written in lower case.
_CLEAR_STREET_TYPE = (
    r"(?i:C/|Calle|Carrer|Avda\.?|Avenida|Avinguda|Av\.?|Pl\.|Plaza|Pla[çc]a|Pza\.?|Plza\.?"
    r"|Pg\.|Pº|Passeig|Rambla|Ctra\.?|R[úu]a|Glorieta|Traves[íi]a|Travessera|Urb\.|Urbanizaci[óo]n)"
)
# Street types that are also ordinary words ("el camino es largo") or initials ("C."): the
# name must start with a capital letter.
_AMBIGUOUS_STREET_TYPE = (
    r"(?:(?i:Paseo|Camino|Ronda|Carretera|V[íi]a|Callej[óo]n|Pol[íi]gono)"
    r"|C\.)"
)
# \u2019 is the typographic apostrophe (as in d\u2019Aragó), written as an escape so it is visible.
_ANY_WORD = r"(?:d['\u2019])?[^\W\d_][\w'\u2019.-]{0,30}"
_CAPITAL_WORD = r"(?:d['\u2019])?[A-ZÁÉÍÓÚÑÜÇÀÈÒÏ][\w'\u2019.-]{0,30}"
_CONNECTOR = r"(?:de|del|la|las|los|el|y|i|do|da|dos|das)"
_ANY_NAME = rf"{_ANY_WORD}(?: {_ANY_WORD}){{0,5}}"
_CAPITAL_NAME = (
    rf"(?:{_CONNECTOR} ){{0,3}}{_CAPITAL_WORD}(?: (?:{_CONNECTOR} ){{0,3}}{_CAPITAL_WORD}){{0,5}}"
)
_NUMBER = r"(?:(?:n\.?[ºo°]\.?|n[úu]m\.?|n[úu]mero) ?)?[0-9]{1,4}[A-Za-z]?(?![^\W_])|s/n(?![^\W_])"
_FLOOR = (
    r"(?:,? ?-? ?[0-9]{1,2}\.?[ºª](?: ?(?:[A-Z]|[0-9]{1,2}\.?[ºª]?)(?![^\W_]))?)?"
    r"(?:,? piso [0-9]{1,2}(?:,? puerta [0-9A-Za-z]{1,2}(?![^\W_]))?)?"
)
_POSTCODE = r"(?:0[1-9]|[1-4][0-9]|5[0-2])[0-9]{3}(?![0-9])"
_ADDRESS = re.compile(
    rf"(?<![^\W_])(?:{_CLEAR_STREET_TYPE} ?{_ANY_NAME}|{_AMBIGUOUS_STREET_TYPE} ?{_CAPITAL_NAME})"
    rf",? (?:{_NUMBER}){_FLOOR}(?P<postcode>,? {_POSTCODE})?"
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
