"""restore_json_text: tool arguments or input written as JSON text, restored (invariant 4)."""

import json

import pytest

from antifaz import mask
from antifaz.detect.types import Confidence, EntityType, Layer, Span
from antifaz.providers.json_walk import restore_json_text
from antifaz.vault import Vault

BS = chr(92)
# A synthetic address with quotes and a backslash: inserting it raw would break the JSON.
ADDRESS = 'Calle "Mayor" 5' + BS + "B"


def _vault() -> Vault:
    text = f"dir {ADDRESS}"
    span = Span(4, len(text), EntityType.ADDRESS, Layer.PATTERN, Confidence.HIGH)
    result = mask(text, detector=lambda _: [span])
    assert result.text == "dir [[ADDRESS_1]]"
    return result.vault


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ('{"a": "en [[ADDRESS_1]]"}', {"a": f"en {ADDRESS}"}),
        ('["[[ADDRESS_1]]", 1]', [ADDRESS, 1]),
        ('"[[ADDRESS_1]]"', ADDRESS),  # a JSON string
        ("42", 42),
        ("null", None),
    ],
)
def test_valid_json_of_any_kind_stays_valid(arguments: str, expected: object) -> None:
    assert json.loads(restore_json_text(arguments, _vault())) == expected


def test_invalid_json_gets_the_value_escaped_as_in_a_json_string() -> None:
    restored = restore_json_text('{"a": "[[ADDRESS_1]]', _vault())  # cut arguments
    assert json.loads(restored + '"}') == {"a": ADDRESS}


def test_plain_text_gets_the_value_escaped_as_in_a_json_string() -> None:
    restored = restore_json_text("no es json: [[ADDRESS_1]]", _vault())
    assert json.loads(f'"{restored}"') == f"no es json: {ADDRESS}"


def test_unknown_placeholders_and_escapes_as_in_restore() -> None:
    restored = restore_json_text("x [[ES_DNI_1]] [[!", _vault())
    assert restored == "x [[ES_DNI_1]] [["
