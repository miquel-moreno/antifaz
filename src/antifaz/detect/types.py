"""Types shared by the detector: entity types, layers, confidence and the Span.

A Span only says where a value is and what it is; it never carries the value itself.
"""

from dataclasses import dataclass
from enum import StrEnum


class EntityType(StrEnum):
    """Kinds of personal data. The definition order breaks exact ties in overlaps."""

    ES_DNI = "ES_DNI"
    ES_NIE = "ES_NIE"
    ES_NIF = "ES_NIF"  # K/L/M NIF for people without DNI or NIE
    ES_CIF = "ES_CIF"
    ES_NSS = "ES_NSS"
    ES_CCC = "ES_CCC"
    IBAN = "IBAN"
    IT_CODICE_FISCALE = "IT_CODICE_FISCALE"
    EU_VAT = "EU_VAT"
    CREDIT_CARD = "CREDIT_CARD"
    EMAIL = "EMAIL"
    IP = "IP"
    PHONE = "PHONE"
    ES_PASSPORT = "ES_PASSPORT"
    ES_PLATE = "ES_PLATE"
    ADDRESS = "ADDRESS"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    PT_NIF = "PT_NIF"
    FR_NIR = "FR_NIR"
    DE_IDNR = "DE_IDNR"


class Layer(StrEnum):
    """Which detector layer found the span."""

    VALIDATOR = "VALIDATOR"
    PATTERN = "PATTERN"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"


@dataclass(frozen=True, slots=True)
class Span:
    """Half-open range [start, end) of the text holding one entity."""

    start: int
    end: int
    type: EntityType
    layer: Layer
    confidence: Confidence
