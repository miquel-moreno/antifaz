"""Invariant 10 for the NSS: python-stdnum has no NSS module, so besides the documented vectors
(tests/data/nss_vectors.md) we check that mod 97 catches every single-digit error.

No official TGSS source publishes the algorithm; vectors derived from the published algorithm.
"""

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from antifaz.detect.validators import nss

SMALL_LIMIT = 10**7


def _control(province: int, number: int) -> int:
    """Reference algorithm, as documented in tests/data/nss_vectors.md."""
    if number < SMALL_LIMIT:
        return (number + province * 10**7) % 97
    return (province * 10**8 + number) % 97


def _nss(province: int, number: int) -> str:
    return f"{province:02d}{number:08d}{_control(province, number):02d}"


valid_nss = st.builds(
    _nss,
    st.integers(min_value=1, max_value=99),
    st.one_of(
        st.integers(min_value=0, max_value=SMALL_LIMIT - 1),  # number < 10^7 branch
        st.integers(min_value=SMALL_LIMIT, max_value=10**8 - 1),  # concatenation branch
    ),
)


@settings(max_examples=2000, deadline=None)
@given(value=valid_nss)
def test_generated_nss_from_both_branches_is_valid(value: str) -> None:
    """Invariant 10: NSS built with the documented algorithm is accepted."""
    assert nss.is_valid(value) is True


@settings(max_examples=3000, deadline=None)
@given(value=valid_nss, position=st.integers(0, 11), shift=st.integers(1, 9))
def test_changing_any_single_digit_of_a_valid_nss_makes_it_invalid(
    value: str, position: int, shift: int
) -> None:
    """Invariant 10: mod 97 catches every single-digit error.

    Changing the first digit of the number can move it across the 10^7 limit, which switches
    the formula; that case is not a plain single-digit error for mod 97, so it is excluded.
    """
    new_digit = str((int(value[position]) + shift) % 10)
    mutated = value[:position] + new_digit + value[position + 1 :]
    assume((int(value[2:10]) < SMALL_LIMIT) == (int(mutated[2:10]) < SMALL_LIMIT))
    assert nss.is_valid(mutated) is False
