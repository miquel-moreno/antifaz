"""Helpers shared by the validators: normalise formatting and check ASCII digits."""

_SEPARATORS = str.maketrans("", "", " .-/\r\n")


def compact(value: str) -> str:
    """Remove spaces, dots, hyphens, slashes and line breaks, and uppercase."""
    return value.translate(_SEPARATORS).upper()


def is_ascii_digits(value: str) -> bool:
    """True only for a non-empty string of ASCII 0-9 digits.

    `str.isdigit()` alone also accepts full-width and Arabic-Indic digits, which int()
    would silently convert: an identifier written with them is not treated as valid.
    """
    return value.isascii() and value.isdigit()
