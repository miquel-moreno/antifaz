"""The closed JSON schema of POST /panel/api/try (issue 53, PR 11d-a): web/try_response.py.

Every model refuses extra fields, is strict and hides the input in its validation errors (the
input holds personal values: a broken answer must never print them). A blocked answer carries
no sent or reply segments; outcome and blocked_reason agree. Markers have the placeholder form.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from antifaz import EntityType
from antifaz.web.try_response import TryResponse
from tests.conftest import SENTINEL_DNI


def _good(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "outcome": "masked",
        "blocked_reason": None,
        "length_utf16": 14,
        "entities": [{"type": EntityType.ES_DNI, "start": 4, "end": 13, "hidden": True}],
        "sent": [{"text": "DNI "}, {"marker": "[[ES_DNI_1]]", "type": EntityType.ES_DNI}],
        "reply": [
            {"text": "ok "},
            {"marker": "[[ES_DNI_1]]", "type": EntityType.ES_DNI, "value": SENTINEL_DNI},
        ],
        "counts": [{"type": EntityType.ES_DNI, "found": 1, "hidden": 1}],
        "policy_version": "builtin-1",
        "ner": "off",
        "names_checked": False,
    }
    body.update(changes)
    return body


def test_a_good_answer_validates_and_dumps_to_plain_json() -> None:
    dumped = TryResponse.model_validate(_good()).model_dump(mode="json")

    assert dumped == {
        **_good(),
        "entities": [{"type": "ES_DNI", "start": 4, "end": 13, "hidden": True}],
        "sent": [{"text": "DNI "}, {"marker": "[[ES_DNI_1]]", "type": "ES_DNI"}],
        "reply": [
            {"text": "ok "},
            {"marker": "[[ES_DNI_1]]", "type": "ES_DNI", "value": SENTINEL_DNI},
        ],
        "counts": [{"type": "ES_DNI", "found": 1, "hidden": 1}],
    }


def test_a_blocked_answer() -> None:
    body = _good(outcome="blocked", blocked_reason="detector_failed", sent=[], reply=[])

    assert TryResponse.model_validate(body).outcome == "blocked"


@pytest.mark.parametrize(
    "changes",
    [
        {"extra": SENTINEL_DNI},
        {"text": SENTINEL_DNI},  # the original text is never a field
        {"outcome": "ok"},
        {"outcome": "blocked", "blocked_reason": None, "sent": [], "reply": []},
        {"blocked_reason": "egress_guard"},  # masked with a reason
        {"outcome": "blocked", "blocked_reason": "egress_guard"},  # blocked with segments
        {"blocked_reason": "other"},
        {"length_utf16": -1},
        {"length_utf16": "14"},
        {"length_utf16": 14.0},
        {"elapsed_ms": 3},  # no timing in the answer (review: a timing oracle)
        {"entities": [{"type": EntityType.ES_DNI, "start": 4, "end": 13, "hidden": 1}]},
        {"entities": [{"type": "NOT_A_TYPE", "start": 4, "end": 13, "hidden": True}]},
        {
            "entities": [
                {"type": EntityType.ES_DNI, "start": 4, "end": 13, "hidden": True, "value": "x"}
            ]
        },
        {"sent": [{"text": "a", "marker": "[[ES_DNI_1]]"}]},
        {"sent": [{"marker": SENTINEL_DNI, "type": EntityType.ES_DNI}]},
        {"sent": [{"marker": "[[ES_DNI_1]]", "type": EntityType.ES_DNI, "value": SENTINEL_DNI}]},
        {"sent": [{"marker": "[[es_dni_1]]", "type": EntityType.ES_DNI}]},
        {"sent": [{"value": SENTINEL_DNI}]},
        {"reply": [{"marker": "[[ES_DNI_1]]", "type": EntityType.ES_DNI}]},  # no value
        {"counts": [{"type": EntityType.ES_DNI, "found": 1, "hidden": 2}]},
        {"counts": [{"type": EntityType.ES_DNI, "found": 0, "hidden": 0}]},
        {"policy_version": f"v {SENTINEL_DNI} <script>"},
        {"ner": "maybe"},
        # 11d-b: whether the name detector really checked this text, a real boolean.
        {"names_checked": 1},
        {"names_checked": "false"},
        {"names_checked": None},
        {"names_checked": True},  # with the NER off for the whole gateway it never can
        {"entities": [{"type": EntityType.ES_DNI, "start": 9, "end": 4, "hidden": True}]},
    ],
)
def test_anything_else_is_refused_without_printing_the_input(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as caught:
        TryResponse.model_validate(_good(**changes))

    assert SENTINEL_DNI not in str(caught.value)
    assert SENTINEL_DNI not in repr(caught.value)


def test_names_checked_is_required_and_true_only_with_the_ner_there() -> None:
    body = _good()
    del body["names_checked"]
    with pytest.raises(ValidationError):
        TryResponse.model_validate(body)

    assert TryResponse.model_validate(_good(ner="ok", names_checked=True)).names_checked
    assert not TryResponse.model_validate(_good(ner="ok", names_checked=False)).names_checked


def test_every_model_forbids_extras_is_strict_and_hides_the_input() -> None:
    from antifaz.web import try_response

    models = [
        value
        for value in vars(try_response).values()
        if isinstance(value, type)
        and issubclass(value, try_response.BaseModel)
        and value is not try_response.BaseModel
    ]
    assert len(models) >= 6
    for model in models:
        config = model.model_config
        assert config.get("extra") == "forbid", model
        assert config.get("strict") is True, model
        assert config.get("hide_input_in_errors") is True, model
        assert config.get("frozen") is True, model


def test_the_policy_version_rule_is_the_policy_s_own() -> None:
    from antifaz.policy import DEFAULT_POLICY, VERSION_PATTERN, Policy
    from antifaz.web.try_response import POLICY_VERSION_PATTERN

    assert POLICY_VERSION_PATTERN == VERSION_PATTERN
    assert TryResponse.model_validate(_good(policy_version=DEFAULT_POLICY.version))
    for bad in ("", "v 1", "v1/../x", "a" * 65, "-v1", f"v{SENTINEL_DNI}<"):
        with pytest.raises(ValueError):
            Policy(version=bad)
