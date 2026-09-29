"""Invariant 10: our validators agree with python-stdnum (the oracle, ADR-0005).

Each identifier is checked on 5000 generated values that mix two strategies:
(a) random body + random control character: mostly invalid values, plus wrong lengths;
(b) random body + the control computed by stdnum: values that must be valid.
Values are generated in compact form (stdnum.es.dni.compact does not remove dots).
All values are synthetic.
"""

import string
from collections.abc import Callable

from hypothesis import given, settings
from hypothesis import strategies as st
from stdnum import iban as std_iban
from stdnum.es import ccc as std_ccc
from stdnum.es import cif as std_cif
from stdnum.es import dni as std_dni
from stdnum.es import nie as std_nie
from stdnum.es import nif as std_nif

from antifaz.detect.validators import ccc, cif, dni, iban, nie, nif_klm

EXAMPLES = settings(max_examples=5000, deadline=None)

CONTROL_CHARS = string.digits + string.ascii_uppercase
CIF_LETTERS = "ABCDEFGHJNPQRSUVW"

# A generated case: the value, and whether it was built to be valid (strategy b) or not (a).
Case = tuple[str, bool]


def digits(min_size: int, max_size: int | None = None) -> st.SearchStrategy[str]:
    size = min_size if max_size is None else max_size
    return st.text(string.digits, min_size=min_size, max_size=size)


def control() -> st.SearchStrategy[str]:
    return st.sampled_from(CONTROL_CHARS)


def random_case(*parts: st.SearchStrategy[str]) -> st.SearchStrategy[Case]:
    """Strategy (a): concatenate random parts; validity unknown (mostly invalid)."""
    return st.tuples(*parts).map(lambda p: ("".join(p), False))


def valid_case(
    builder: Callable[..., str], *parts: st.SearchStrategy[str]
) -> st.SearchStrategy[Case]:
    """Strategy (b): build a value whose control comes from stdnum; it must be valid."""
    return st.tuples(*parts).map(lambda p: (builder(*p), True))


def check_against_oracle(
    ours: Callable[[str], bool], oracle: Callable[[str], bool], case: Case
) -> None:
    value, built_valid = case
    result = ours(value)
    assert result is oracle(value), value
    if built_valid:
        assert result is True, value


# --- DNI -----------------------------------------------------------------------------------

dni_cases = st.one_of(
    random_case(digits(7, 9), control()),
    valid_case(lambda body: body + std_dni.calc_check_digit(body), digits(8)),
)


@EXAMPLES
@given(case=dni_cases)
def test_dni_agrees_with_stdnum(case: Case) -> None:
    """Invariant 10: DNI == stdnum.es.dni."""
    check_against_oracle(dni.is_valid, std_dni.is_valid, case)


# --- NIE -----------------------------------------------------------------------------------

nie_cases = st.one_of(
    random_case(st.sampled_from("XYZW"), digits(6, 8), control()),
    valid_case(
        lambda prefix, body: prefix + body + std_nie.calc_check_digit(prefix + body),
        st.sampled_from("XYZ"),
        digits(7),
    ),
)


@EXAMPLES
@given(case=nie_cases)
def test_nie_agrees_with_stdnum(case: Case) -> None:
    """Invariant 10: NIE == stdnum.es.nie."""
    check_against_oracle(nie.is_valid, std_nie.is_valid, case)


# --- NIF K/L/M (compared with stdnum.es.nif, restricted to K/L/M) --------------------------

nif_klm_cases = st.one_of(
    random_case(st.sampled_from("KLM"), digits(6, 8), control()),
    valid_case(
        lambda prefix, body: prefix + body + std_dni.calc_check_digit(body),
        st.sampled_from("KLM"),
        digits(7),
    ),
)


@EXAMPLES
@given(case=nif_klm_cases)
def test_nif_klm_agrees_with_stdnum(case: Case) -> None:
    """Invariant 10: NIF K/L/M == stdnum.es.nif for values starting with K, L or M."""
    check_against_oracle(nif_klm.is_valid, std_nif.is_valid, case)


# --- CIF (digit or letter control for any valid initial letter, ADR-0009) ------------------


def _valid_cif(prefix: str, body: str, use_letter: str) -> str:
    # calc_check_digits returns both candidates: digit first, then letter.
    candidates = std_cif.calc_check_digits(prefix + body)
    return prefix + body + (candidates[1] if use_letter else candidates[0])


cif_cases = st.one_of(
    random_case(st.sampled_from(string.ascii_uppercase), digits(6, 8), control()),
    valid_case(_valid_cif, st.sampled_from(CIF_LETTERS), digits(7), st.sampled_from(["", "L"])),
)


@EXAMPLES
@given(case=cif_cases)
def test_cif_agrees_with_stdnum(case: Case) -> None:
    """Invariant 10: CIF == stdnum.es.cif (includes invalid initial letters)."""
    check_against_oracle(cif.is_valid, std_cif.is_valid, case)


# --- CCC -----------------------------------------------------------------------------------


def _valid_ccc(entity_office: str, account: str) -> str:
    return entity_office + std_ccc.calc_check_digits(entity_office + "00" + account) + account


ccc_cases = st.one_of(
    random_case(digits(19, 21)),
    valid_case(_valid_ccc, digits(8), digits(10)),
)


@EXAMPLES
@given(case=ccc_cases)
def test_ccc_agrees_with_stdnum(case: Case) -> None:
    """Invariant 10: CCC == stdnum.es.ccc."""
    check_against_oracle(ccc.is_valid, std_ccc.is_valid, case)


# --- IBAN (ES) -----------------------------------------------------------------------------


def _iban_with_computed_check(bban: str) -> str:
    return "ES" + std_iban.calc_check_digits("ES00" + bban) + bban


iban_cases = st.one_of(
    random_case(st.just("ES"), digits(2), digits(19, 21)),
    valid_case(_iban_with_computed_check, st.builds(_valid_ccc, digits(8), digits(10))),
    # Correct mod-97 digits around a random BBAN: mostly an invalid CCC inside, so the IBAN is
    # invalid for Spain (stdnum.iban checks the country part too).
    random_case(digits(20).map(_iban_with_computed_check)),
)


@EXAMPLES
@given(case=iban_cases)
def test_spanish_iban_agrees_with_stdnum(case: Case) -> None:
    """Invariant 10: Spanish IBAN == stdnum.iban (mod 97 plus a valid CCC)."""
    check_against_oracle(iban.is_valid, std_iban.is_valid, case)
