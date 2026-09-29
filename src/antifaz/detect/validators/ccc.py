"""CCC: Spanish bank account code, 20 digits with two mod-11 control digits.

entity (4) + office (4) + controls (2) + account (10). The first control covers
"00" + entity + office, the second the account, with weights 1, 2, 4, 8, 5, 10, 9, 7, 3, 6
(2^i mod 11); remainder 0 -> 0, 1 -> 1, otherwise 11 - remainder.
Source: AEB, "Identificación normalizada de cuentas CCC/IBAN" (2001).
"""

from antifaz.detect.validators._ascii import compact, is_ascii_digits

WEIGHTS = (1, 2, 4, 8, 5, 10, 9, 7, 3, 6)


def control_digit(ten_digits: str) -> str:
    remainder = sum(int(d) * w for d, w in zip(ten_digits, WEIGHTS, strict=True)) % 11
    return str(remainder if remainder < 2 else 11 - remainder)


def is_valid(value: str) -> bool:
    """Return True if the value is a valid CCC, whatever its formatting. Never raises."""
    number = compact(value)
    if len(number) != 20 or not is_ascii_digits(number):
        return False
    expected = control_digit("00" + number[:8]) + control_digit(number[10:])
    return number[8:10] == expected
