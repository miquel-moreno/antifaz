import dataclasses

import pytest

from antifaz.detect.types import Confidence, EntityType, Layer, Span
from tests.conftest import SENTINEL_DNI


def _span() -> Span:
    return Span(0, 9, EntityType.ES_DNI, Layer.VALIDATOR, Confidence.HIGH)


def test_span_has_exactly_position_type_layer_and_confidence() -> None:
    names = [f.name for f in dataclasses.fields(Span)]
    assert names == ["start", "end", "type", "layer", "confidence"]


def test_span_cannot_carry_the_identifier_value() -> None:
    span = _span()
    text = f"DNI {SENTINEL_DNI}"
    assert SENTINEL_DNI not in repr(span)
    assert SENTINEL_DNI not in str(span)
    assert text not in repr(span)
    assert not hasattr(span, "__dict__")  # slots: no extra attribute can be attached


def test_span_is_immutable() -> None:
    span = _span()
    with pytest.raises(dataclasses.FrozenInstanceError):
        span.start = 1  # type: ignore[misc]


def test_equal_spans_are_hashable_and_equal() -> None:
    assert len({_span(), _span()}) == 1


def test_entity_types_cover_the_identifiers_of_this_stage() -> None:
    assert [t.value for t in EntityType] == [
        "ES_DNI",
        "ES_NIE",
        "ES_NIF",
        "ES_CIF",
        "ES_NSS",
        "ES_CCC",
        "IBAN",
        "IT_CODICE_FISCALE",
        "EU_VAT",
        "CREDIT_CARD",
    ]
    assert {layer.value for layer in Layer} == {"VALIDATOR", "PATTERN"}
    assert {c.value for c in Confidence} == {"HIGH", "MEDIUM"}
