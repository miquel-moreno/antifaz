"""Invariant 4: restored tool arguments are valid JSON and equal to restoring the parsed JSON.

Hypothesis builds arguments with quotes, backslashes, brackets and synthetic personal data;
a fake provider echoes the masked arguments back and the client must get the originals.
"""

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from antifaz.providers.openai_chat import mask_request, restore_response
from antifaz.restore import restore

VALUES = ["12345678Z", "X1234567L", "ana@example.com", "612 345 678"]
FILLER = ['"', "\\", "\\u0041", "[[", "[[ES_DNI_1]]", "]]", "!", "\n", "ñ", "​", "{", "a b"]

text = st.lists(st.sampled_from(VALUES) | st.sampled_from(FILLER), max_size=6).map("".join)
leaf = text | st.integers() | st.booleans() | st.none()
keys = st.sampled_from(["a", "nota", "dni", "lista"])
json_values = st.recursive(
    leaf,
    lambda children: st.lists(children, max_size=3) | st.dictionaries(keys, children, max_size=3),
    max_leaves=10,
)
arguments = st.dictionaries(keys, json_values, max_size=4)


def _request(args: str) -> dict[str, object]:
    call = {"id": "c", "type": "function", "function": {"name": "f", "arguments": args}}
    return {"messages": [{"role": "assistant", "tool_calls": [call]}]}


def _restore_parsed(node: object, vault: object) -> object:
    if isinstance(node, str):
        return restore(node, vault)  # type: ignore[arg-type]
    if isinstance(node, list):
        return [_restore_parsed(item, vault) for item in node]
    if isinstance(node, dict):
        return {key: _restore_parsed(value, vault) for key, value in node.items()}
    return node


@settings(max_examples=200, deadline=None)
@given(arguments, st.booleans())
def test_tool_arguments_round_trip_as_valid_json(args: dict[str, object], ascii_: bool) -> None:
    original = json.dumps(args, ensure_ascii=ascii_)
    masked, vault = mask_request(_request(original))
    sent = masked["messages"][0]["tool_calls"][0]["function"]["arguments"]  # type: ignore[index]
    response = {
        "choices": [{"index": 0, "message": {"tool_calls": [{"function": {"arguments": sent}}]}}]
    }

    restored = restore_response(response, vault)

    got = restored["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]  # type: ignore[index]
    assert json.loads(got) == args
    assert json.loads(got) == _restore_parsed(json.loads(sent), vault)
