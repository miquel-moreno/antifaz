"""NIE: X/Y/Z (as 0/1/2), 7 digits and the DNI control letter."""

from antifaz.detect.validators._ascii import compact, is_ascii_digits
from antifaz.detect.validators.dni import control_letter

PREFIXES = "XYZ"


def is_valid(value: str) -> bool:
    """Return True if the value is a valid NIE, whatever its formatting. Never raises."""
    number = compact(value)
    if len(number) != 9 or number[0] not in PREFIXES or not is_ascii_digits(number[1:8]):
        return False
    return number[8] == control_letter(str(PREFIXES.index(number[0])) + number[1:8])
