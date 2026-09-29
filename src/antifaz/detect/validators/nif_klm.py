"""NIF K/L/M: letter, 7 digits and the DNI control letter over those 7 digits.

K: Spanish under 14 living in Spain; L: Spanish living abroad; M: foreigners without NIE
(RD 1065/2007, arts. 19-20). The regulation says "seven alphanumeric characters" but does
not publish the algorithm: like python-stdnum, 7 digits and the DNI letter (ADR-0009).
"""

from antifaz.detect.validators._ascii import compact, is_ascii_digits
from antifaz.detect.validators.dni import control_letter


def is_valid(value: str) -> bool:
    """Return True if the value is a valid K/L/M NIF, whatever its formatting. Never raises."""
    number = compact(value)
    return (
        len(number) == 9
        and number[0] in "KLM"
        and is_ascii_digits(number[1:8])
        and number[8] == control_letter(number[1:8])
    )
