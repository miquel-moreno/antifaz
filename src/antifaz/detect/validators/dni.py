"""DNI: 8 digits and a control letter (number mod 23).

Source: Ministerio del Interior, "Cálculo del dígito de control del NIF/NIE".
"""

from antifaz.detect.validators._ascii import compact, is_ascii_digits

LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def control_letter(digits: str) -> str:
    """Control letter for a string of ASCII digits (also used by NIE and NIF K/L/M)."""
    return LETTERS[int(digits) % 23]


def is_valid(value: str) -> bool:
    """Return True if the value is a valid DNI, whatever its formatting. Never raises."""
    number = compact(value)
    return (
        len(number) == 9 and is_ascii_digits(number[:8]) and number[8] == control_letter(number[:8])
    )
