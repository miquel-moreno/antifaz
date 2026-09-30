"""Invariants 3, 4 and 9 over whole streams: bytes cut anywhere, UTF-8 characters included.

The provider's answer is built from the masked text, split into deltas at random points, and
the SSE bytes are cut at random points too. What the client rebuilds must equal restore() of
the whole text; tool input must be valid JSON equal to restoring the parsed JSON; thinking
must come out byte for byte.
"""

import codecs
import json
from itertools import pairwise
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from antifaz import mask
from antifaz.providers.anthropic_stream import AnthropicMessagesStream
from antifaz.providers.json_walk import restore_strings
from antifaz.providers.openai_stream import OpenAIChatStream
from antifaz.providers.sse import SSEParser
from antifaz.restore import restore
from tests.integration.fakes import (
    anthropic_event,
    anthropic_sse,
    anthropic_text,
    openai_sse,
    openai_text,
    sse_payloads,
)

VALUES = ["12345678Z", "X1234567L", "ana@example.com"]
FILLER = ["ñ", "😀", "€", "[[", "[[ES_DNI_9]]", "[[ es_dni_1 ]]", "]]", "!", '"', "\\", " ", "a"]
texts = st.lists(st.sampled_from(VALUES) | st.sampled_from(FILLER), max_size=10).map("".join)


def _cut(data: st.DataObject, value: str | bytes, size: int = 8) -> list[Any]:
    points = sorted(data.draw(st.sets(st.integers(0, len(value)), max_size=size)))
    bounds = [0, *points, len(value)]
    return [value[a:b] for a, b in pairwise(bounds)]


def _relay(transformer: Any, chunks: list[bytes]) -> str:
    decoder = codecs.getincrementaldecoder("utf-8")()
    parser = SSEParser()
    out = []
    for chunk in chunks:
        out.extend(transformer.event(event) for event in parser.feed(decoder.decode(chunk)))
    parser.feed(decoder.decode(b"", final=True))
    assert parser.finished
    return "".join(out) + transformer.end()


@settings(max_examples=250, deadline=None)
@given(original=texts, data=st.data())
def test_openai_any_cut_gives_the_restored_text(original: str, data: st.DataObject) -> None:
    result = mask(original)
    stream = openai_sse(_cut(data, result.text)).encode()
    out = _relay(OpenAIChatStream(result.vault), _cut(data, stream))
    assert openai_text(out) == restore(result.text, result.vault) == original


@settings(max_examples=250, deadline=None)
@given(original=texts, data=st.data())
def test_anthropic_any_cut_gives_the_restored_text(original: str, data: st.DataObject) -> None:
    result = mask(original)
    stream = anthropic_sse(_cut(data, result.text)).encode()
    out = _relay(AnthropicMessagesStream(result.vault), _cut(data, stream))
    assert anthropic_text(out) == original


@settings(max_examples=150, deadline=None)
@given(args=st.dictionaries(st.sampled_from(["a", "b"]), texts, max_size=3), data=st.data())
def test_anthropic_tool_input_and_thinking(args: dict[str, str], data: st.DataObject) -> None:
    # The model only saw masked values (the proxy masks JSON values, not JSON text).
    result = mask(list(args.values()))
    masked_args = json.dumps(dict(zip(args, result.texts, strict=True)))
    thinking = anthropic_event(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": masked_args},
        }
    )
    tool = [
        anthropic_event(
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": piece},
            }
        )
        for piece in _cut(data, masked_args)
    ]
    block = {"type": "tool_use", "id": "t", "name": "f", "input": {}}
    start = anthropic_event({"type": "content_block_start", "index": 1, "content_block": block})
    stream = (
        thinking
        + start
        + "".join(tool)
        + anthropic_event({"type": "content_block_stop", "index": 1})
        + anthropic_event({"type": "message_stop"})
    ).encode()
    out = _relay(AnthropicMessagesStream(result.vault), _cut(data, stream))

    assert thinking in out  # invariant 9: byte for byte
    (partial,) = [
        p["delta"]["partial_json"]
        for p in sse_payloads(out)
        if p.get("delta", {}).get("type") == "input_json_delta"
    ]
    parsed = json.loads(partial)  # invariant 4: valid JSON
    assert parsed == restore_strings(json.loads(masked_args), result.vault) == args
