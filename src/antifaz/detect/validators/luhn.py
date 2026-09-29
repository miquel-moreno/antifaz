"""Luhn check digit (payment card numbers, and the CIF control)."""

from antifaz.detect.validators._ascii import compact, is_ascii_digits


def checksum(digits: str) -> int:
    """Luhn sum mod 10 of a string of ASCII digits: 0 means the last digit checks out."""
    total = 0
    for position, char in enumerate(reversed(digits)):
        digit = int(char)
        if position % 2:
            digit *= 2
            digit -= 9 if digit > 9 else 0
        total += digit
    return total % 10


def check_digit(digits: str) -> int:
    """The digit that, appended to `digits`, makes a valid Luhn number."""
    return -checksum(digits + "0") % 10


def is_valid(value: str) -> bool:
    """Return True if the value is a valid Luhn number, whatever its formatting. Never raises."""
    number = compact(value)
    return len(number) >= 2 and is_ascii_digits(number) and checksum(number) == 0
