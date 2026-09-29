"""IBAN (ISO 13616, mod 97); Spanish IBANs must also hold a valid CCC.

The per-country BBAN structure (e.g. Spain "4!n4!n1!n1!n10!n") comes from the IBAN
registry data shipped with python-stdnum, so no country table is written by hand.
"""

import re
from functools import cache

from stdnum import numdb

from antifaz.detect.validators import ccc
from antifaz.detect.validators._ascii import compact, is_ascii_digits

_TOKEN = re.compile(r"(\d+)(!?)([nace])")
_CHARS = {"n": "[0-9]", "a": "[A-Z]", "c": "[A-Za-z0-9]", "e": " "}


def _bban_structure(country: str) -> str:
    info = numdb.get("iban").info(country)
    return str(info[0][1].get("bban", "")) if info and info[0][1] else ""


@cache
def _bban_regex(country: str) -> re.Pattern[str] | None:
    structure = _bban_structure(country)
    if not structure:
        return None
    parts = [
        f"{_CHARS[kind]}{{{count}}}" if fixed else f"{_CHARS[kind]}{{1,{count}}}"
        for count, fixed, kind in _TOKEN.findall(structure)
    ]
    return re.compile("".join(parts))


@cache
def length(country: str) -> int | None:
    """Total IBAN length for a country with a fixed-length structure, else None."""
    tokens = _TOKEN.findall(_bban_structure(country))
    if not tokens or any(not fixed for _, fixed, _ in tokens):
        return None
    return 4 + sum(int(count) for count, _, _ in tokens)


def _mod97(number: str) -> int:
    rearranged = number[4:] + number[:4]
    return int("".join(str(int(char, 36)) for char in rearranged)) % 97


def is_valid(value: str) -> bool:
    """Return True if the value is a valid IBAN, whatever its formatting. Never raises."""
    number = compact(value)
    if len(number) < 5 or not number.isascii() or not number.isalnum():
        return False
    country, check, bban = number[:2], number[2:4], number[4:]
    if not country.isalpha() or not is_ascii_digits(check):
        return False
    pattern = _bban_regex(country)
    if pattern is None or not pattern.fullmatch(bban) or _mod97(number) != 1:
        return False
    return country != "ES" or ccc.is_valid(bban)
