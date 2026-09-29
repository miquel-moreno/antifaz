"""EU identifiers validated with python-stdnum (ADR-0005)."""

from stdnum.eu import vat
from stdnum.it import codicefiscale


def it_codice_fiscale_is_valid(value: str) -> bool:
    """Return True if the value is a valid Italian codice fiscale. Never raises."""
    try:
        return bool(codicefiscale.is_valid(value))
    except Exception:  # stdnum should not raise here; never let a value escape in an error
        return False


def eu_vat_is_valid(value: str) -> bool:
    """Return True if the value is a valid EU VAT number. Never raises."""
    try:
        return bool(vat.is_valid(value))
    except Exception:  # stdnum should not raise here; never let a value escape in an error
        return False
