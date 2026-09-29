"""Spanish identifiers: DNI, NIE, NIF K/L/M, CIF and CCC. All values are synthetic."""

import pytest

from antifaz.detect.validators import ccc, cif, dni, nie, nif_klm

# --- DNI -----------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["12345678Z", "12345678-Z", "12.345.678-Z", "12345678 z"])
def test_dni_with_correct_letter_is_valid_in_any_usual_format(value: str) -> None:
    assert dni.is_valid(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "12345678A",  # wrong letter (12345678 mod 23 = 14 -> Z)
        "1234567Z",  # 7 digits
        "123456789Z",  # 9 digits
        "12345678",  # letter missing
        "Z12345678",  # letter in the wrong place
        "1234567AZ",
    ],
)
def test_dni_with_wrong_letter_or_length_is_not_valid(value: str) -> None:
    assert dni.is_valid(value) is False


# --- NIE -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "X2482300W",  # X -> 0: 02482300 mod 23 -> W
        "x-2482300-w",
        "Y2482300Q",  # Y -> 1: 12482300 mod 23 -> Q
        "Z2482300F",  # Z -> 2: 22482300 mod 23 -> F
    ],
)
def test_nie_maps_x_y_z_to_0_1_2_before_the_dni_letter(value: str) -> None:
    assert nie.is_valid(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "X2482300A",  # wrong letter
        "Y2482300W",  # the X letter is wrong for Y
        "W2482300W",  # W is not a NIE prefix
        "X248230W",  # too short
        "X24823000W",  # too long
    ],
)
def test_nie_with_wrong_prefix_letter_or_length_is_not_valid(value: str) -> None:
    assert nie.is_valid(value) is False


def test_a_dni_is_not_a_nie() -> None:
    assert nie.is_valid("12345678Z") is False


# --- NIF K/L/M -----------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["K1234567L", "L1234567L", "M1234567L", "m-1234567-l"])
def test_nif_klm_uses_the_dni_letter_over_its_seven_digits(value: str) -> None:
    # 1234567 mod 23 = 19 -> L
    assert nif_klm.is_valid(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "K1234567A",  # wrong letter
        "K123456AL",  # non-digit body
        "K12345678L",  # too long
        "X1234567L",  # X belongs to the NIE, not to the K/L/M NIF
        "12345678Z",  # a DNI is not a K/L/M NIF
    ],
)
def test_nif_klm_with_wrong_letter_body_or_prefix_is_not_valid(value: str) -> None:
    assert nif_klm.is_valid(value) is False


# --- CIF -----------------------------------------------------------------------------------
# Decision approved by Miquel (ADR-0009): accept exactly what python-stdnum accepts, i.e. the
# control may be a digit or a letter for any valid initial letter (ABCDEFGHJNPQRSUVW).


@pytest.mark.parametrize(
    "value",
    [
        "P12345674",  # digit control for P1234567
        "P1234567D",  # letter control for P1234567 ('JABCDEFGHI'[4])
        "P-1234567-D",
        "B64717838",  # example from python-stdnum's es.nif docstring
        "J99216582",  # example from python-stdnum's es.cif docstring
    ],
)
def test_cif_accepts_digit_or_letter_control(value: str) -> None:
    assert cif.is_valid(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "P12345675",  # wrong digit control
        "P1234567E",  # wrong letter control
        "O12345674",  # O is not a valid organisation letter
        "M12345674",  # M is a K/L/M NIF prefix, not a CIF
        "M1234567L",  # valid K/L/M NIF, but not a CIF
        "P1234567",  # control missing
        "P123456744",  # too long
        "PA2345674",  # non-digit body
    ],
)
def test_cif_with_wrong_control_or_initial_letter_is_not_valid(value: str) -> None:
    assert cif.is_valid(value) is False


# --- CCC -----------------------------------------------------------------------------------
# Vector from the AEB (Spanish Banking Association) documentation. Its PDF has an erratum that
# writes the office as "0354"; the correct vector, which passes both mod-11 checks, is 0345.


@pytest.mark.parametrize(
    "value", ["0012 0345 03 0000067890", "00120345030000067890", "0012-0345-03-0000067890"]
)
def test_ccc_aeb_vector_is_valid(value: str) -> None:
    assert ccc.is_valid(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "0012 0345 03 0000067891",  # last digit changed
        "0012 0345 13 0000067890",  # first control digit changed
        "0012 0354 03 0000067890",  # the erratum in the AEB PDF
        "0012 0345 03 000006789",  # 19 digits
        "0012 0345 03 00000678900",  # 21 digits
        "0012 0345 03 000006789A",
    ],
)
def test_ccc_with_one_changed_digit_or_wrong_length_is_not_valid(value: str) -> None:
    assert ccc.is_valid(value) is False
