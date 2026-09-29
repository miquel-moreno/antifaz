"""CIF: organisation letter, 7 digits and a control digit or letter (ADR-0009).

Orden EHA/451/2008: organisation letter + 7 digits + "a control character", without the
algorithm. The usual one is Luhn over the 7 digits, written as a digit or as the letter
"JABCDEFGHI"[digit]. No official rule says which letters use which form, so, like
python-stdnum, either is accepted for every letter.
"""

from antifaz.detect.validators import luhn
from antifaz.detect.validators._ascii import compact, is_ascii_digits

ORGANISATION_LETTERS = "ABCDEFGHJNPQRSUVW"
CONTROL_LETTERS = "JABCDEFGHI"


def is_valid(value: str) -> bool:
    """Return True if the value is a valid CIF, whatever its formatting. Never raises."""
    number = compact(value)
    if len(number) != 9 or number[0] not in ORGANISATION_LETTERS:
        return False
    if not is_ascii_digits(number[1:8]):
        return False
    check = luhn.check_digit(number[1:8])
    return number[8] in (str(check), CONTROL_LETTERS[check])
