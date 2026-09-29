"""mask() and restore(): placeholders, escaping of [[, policy and detector failures."""

from collections.abc import Sequence

import pytest

from antifaz import DetectorFailed, EntityType, MaskResult, mask, restore
from antifaz.detect.types import Confidence, Layer, Span
from tests.conftest import SENTINEL_DNI

EMAIL = "ana@example.com"
IBAN = "ES9121000418450200051332"


def test_dni_is_replaced_by_a_placeholder_and_restored() -> None:
    result = mask(f"Mi DNI es {SENTINEL_DNI}.")
    assert result.text == "Mi DNI es [[ES_DNI_1]]."
    assert SENTINEL_DNI not in result.text
    assert result.hidden == (EntityType.ES_DNI,)
    assert restore(result.text, result.vault) == f"Mi DNI es {SENTINEL_DNI}."


def test_same_value_gets_the_same_placeholder_across_texts() -> None:
    result = mask([f"{SENTINEL_DNI} y {EMAIL}", f"otra vez {SENTINEL_DNI}, y 87654321X"])
    assert result.texts == (
        "[[ES_DNI_1]] y [[EMAIL_1]]",
        "otra vez [[ES_DNI_1]], y [[ES_DNI_2]]",
    )
    assert result.hidden == (EntityType.ES_DNI, EntityType.EMAIL)


def test_text_property_needs_a_single_input() -> None:
    result = mask(["a", "b"])
    with pytest.raises(ValueError, match="texts"):
        _ = result.text


def test_cif_is_allowed_by_default_and_nif_klm_is_masked() -> None:
    result = mask("CIF B12345674, NIF K1234567L")
    assert result.text == "CIF B12345674, NIF [[ES_NIF_1]]"


@pytest.mark.parametrize(
    ("user", "escaped"),
    [
        ("a [[b]] c", "a [[!b]] c"),
        ("[[[x", "[[[!x"),
        ("[[!", "[[!!"),
        ("uno [ solo", "uno [ solo"),
        ("[[ES_DNI_1]]", "[[!ES_DNI_1]]"),
        ("[[ [[", "[[! [[!"),
    ],
)
def test_runs_of_two_or_more_brackets_are_escaped_and_round_trip(user: str, escaped: str) -> None:
    result = mask(user)
    assert result.text == escaped
    assert restore(result.text, result.vault) == user


def test_bracket_glued_to_a_placeholder_round_trips() -> None:
    for text in (
        f"[{SENTINEL_DNI}]",
        f"[[{SENTINEL_DNI}]]",
        f"[[[{SENTINEL_DNI}",
        f"{SENTINEL_DNI}!",
    ):
        result = mask(text)
        assert SENTINEL_DNI not in result.text
        assert restore(result.text, result.vault) == text


def test_restore_tolerates_spaces_and_lower_case() -> None:
    result = mask(SENTINEL_DNI)
    assert restore("es [[ es_dni_1 ]].", result.vault) == f"es {SENTINEL_DNI}."


def test_unknown_placeholder_is_left_as_is() -> None:
    result = mask(SENTINEL_DNI)
    assert restore("[[ES_DNI_9]] y [[ foo_1 ]]", result.vault) == "[[ES_DNI_9]] y [[ foo_1 ]]"


def test_vault_of_another_request_restores_nothing() -> None:
    other = mask(EMAIL)
    mine = mask(SENTINEL_DNI)
    assert restore(mine.text, other.vault) == mine.text == "[[ES_DNI_1]]"
    assert restore(other.text, mine.vault) == other.text == "[[EMAIL_1]]"


def test_restored_values_are_not_rescanned() -> None:
    result = mask(
        "x", detector=lambda t: [Span(0, 1, EntityType.EMAIL, Layer.PATTERN, Confidence.HIGH)]
    )
    # The masked value itself looks like a placeholder: it comes back verbatim, once.
    fake = mask(
        "[[EMAIL_1]]",
        detector=lambda t: [Span(0, len(t), EntityType.EMAIL, Layer.PATTERN, Confidence.HIGH)],
    )
    assert restore(fake.text, fake.vault) == "[[EMAIL_1]]"
    assert restore("[[EMAIL_1]]", result.vault) == "x"


def _failing_detector(text: str) -> Sequence[Span]:
    raise RuntimeError(f"boom with {text}")


def test_failing_detector_blocks_without_leaking_the_text() -> None:
    with pytest.raises(DetectorFailed) as info:
        mask(f"DNI {SENTINEL_DNI}", detector=_failing_detector)
    error = info.value
    assert SENTINEL_DNI not in str(error)
    assert SENTINEL_DNI not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__suppress_context__


@pytest.mark.parametrize(
    "spans",
    [
        [Span(0, 99, EntityType.ES_DNI, Layer.VALIDATOR, Confidence.HIGH)],
        [
            Span(0, 3, EntityType.ES_DNI, Layer.VALIDATOR, Confidence.HIGH),
            Span(2, 4, EntityType.EMAIL, Layer.PATTERN, Confidence.HIGH),
        ],
    ],
)
def test_detector_returning_broken_spans_blocks(spans: list[Span]) -> None:
    with pytest.raises(DetectorFailed):
        mask("abcdef", detector=lambda _: spans)


def test_mask_result_repr_does_not_contain_values() -> None:
    result = mask(f"{SENTINEL_DNI} {EMAIL} {IBAN}")
    assert isinstance(result, MaskResult)
    for value in (SENTINEL_DNI, EMAIL, IBAN):
        assert value not in repr(result)
