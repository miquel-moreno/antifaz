"""NSS: 2-digit province, 8-digit number and 2 control digits (mod 97).

If the number is below 10^7 the control is (number + province * 10^7) mod 97; otherwise
it is the 10 digits (province + number) mod 97. No official TGSS source publishes the
algorithm; see tests/data/nss_vectors.md for the secondary sources and worked vectors.
"""

from antifaz.detect.validators._ascii import compact, is_ascii_digits


def control(province: int, number: int) -> int:
    if number < 10**7:
        return (number + province * 10**7) % 97
    return (province * 10**8 + number) % 97


def is_valid(value: str) -> bool:
    """Return True if the value is a valid NSS, whatever its formatting. Never raises."""
    digits = compact(value)
    if len(digits) != 12 or not is_ascii_digits(digits):
        return False
    return int(digits[10:]) == control(int(digits[:2]), int(digits[2:10]))
