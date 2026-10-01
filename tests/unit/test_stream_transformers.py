"""Streamed answers of OpenAI Chat and Anthropic Messages, restored event by event (part 5c)."""

import json
from typing import Any

import pytest

from antifaz import mask
from antifaz.api.streaming import KeyEchoed, KeyWatch
from antifaz.providers.anthropic_stream import AnthropicMessagesStream
from antifaz.providers.json_walk import restore_strings
from antifaz.providers.openai_stream import OpenAIChatStream
from antifaz.providers.sse import MAX_LINE_CHARS, MalformedStream, SSEParser
from antifaz.providers.streaming import MAX_ACCUMULATED_CHARS, StreamCut
from antifaz.restore import StreamLimitExceeded
from antifaz.vault import Vault
from tests.integration.fakes import (
    TEXT_BLOCK_START,
    anthropic_event,
    anthropic_sse,
    anthropic_text,
    openai_sse,
    openai_text,
    sse_payloads,
)

DNI = "12345678Z"  # synthetic, checksum-valid
EMAIL = "ana@example.com"
SIGNATURE = "EqQBCkYIARgCKkBmaXJtYS1mYWxzYS1kZS1wcnVlYmE+/=="
# Pieces of tool arguments, each below the SSE line limit, that together pass the limit.
_BIG_PIECES = ["a" * (MAX_LINE_CHARS // 2)] * (MAX_ACCUMULATED_CHARS // (MAX_LINE_CHARS // 2) + 1)


def _vault() -> Vault:
    result = mask(f"DNI {DNI} correo {EMAIL}")
    assert result.text == "DNI [[ES_DNI_1]] correo [[EMAIL_1]]"
    return result.vault


def _chunk(delta: dict[str, Any], finish: str | None = None) -> str:
    choice = {"index": 0, "delta": delta, "finish_reason": finish}
    chunk = {"id": "c", "object": "chat.completion.chunk", "choices": [choice]}
    return f"data: {json.dumps(chunk)}\n\n"


def _run(transformer: Any, text: str, *, end: bool = True) -> str:
    parser = SSEParser()
    out = "".join(transformer.event(event) for event in parser.feed(text))
    return out + (transformer.end() if end else "")


# --- OpenAI Chat ------------------------------------------------------------------------------


def test_openai_content_is_restored_across_chunks() -> None:
    stream = OpenAIChatStream(_vault())
    out = _run(stream, openai_sse(["Tu DNI es [", "[ES_DN", "I_1]", "] y ", "[[EMAIL_1]]."]))

    assert openai_text(out) == f"Tu DNI es {DNI} y {EMAIL}."
    assert out.endswith("data: [DONE]\n\n")
    assert stream.unknown == 0


def test_openai_placeholder_of_another_request_is_not_restored() -> None:
    out = _run(OpenAIChatStream(_vault()), openai_sse(["[[ES_DNI_2]] [[IBAN_1]]"]))
    assert openai_text(out) == "[[ES_DNI_2]] [[IBAN_1]]"


def test_openai_refusal_is_restored() -> None:
    text = _chunk({"refusal": "No con [[ES_"}) + _chunk({"refusal": "DNI_1]]"}, "stop")
    out = _run(OpenAIChatStream(_vault()), text + "data: [DONE]\n\n")
    assert openai_text(out, "refusal") == f"No con {DNI}"


def test_openai_chunks_without_text_pass_byte_for_byte() -> None:
    usage = 'data: {"id":"c","choices":[],"usage":{"total_tokens":3},"x":"[[ES_DNI_1]]"}\r\n\r\n'
    out = _run(OpenAIChatStream(_vault()), usage + "data: [DONE]\n\n")
    assert out.startswith(usage)  # untouched, even the unknown field with a placeholder


def _tool_chunks(arguments: list[str], *, index: int = 0) -> str:
    first = {
        "index": index,
        "id": "call_1",
        "type": "function",
        "function": {"name": "buscar", "arguments": ""},
    }
    chunks = [{"role": "assistant", "tool_calls": [first]}]
    chunks += [{"tool_calls": [{"index": index, "function": {"arguments": a}}]} for a in arguments]
    events = [_chunk(delta) for delta in chunks] + [_chunk({}, "tool_calls")]
    return "".join(events) + "data: [DONE]\n\n"


def _tool_calls(out: str) -> dict[int, dict[str, Any]]:
    calls: dict[int, dict[str, Any]] = {}
    for payload in sse_payloads(out):
        if not isinstance(payload, dict):
            continue
        for choice in payload.get("choices", []):
            for call in choice.get("delta", {}).get("tool_calls", []):
                entry = calls.setdefault(call["index"], {"arguments": ""})
                function = call.get("function", {})
                entry["arguments"] += function.get("arguments", "")
                for key in ("id", "type"):
                    if key in call:
                        entry[key] = call[key]
                if "name" in function:
                    entry["name"] = function["name"]
    return calls


def test_openai_tool_arguments_are_emitted_restored_when_the_choice_finishes() -> None:
    vault = _vault()
    masked = json.dumps({"dni": "[[ES_DNI_1]]", "nota": 'con "comillas" y [[EMAIL_1]]'})
    out = _run(OpenAIChatStream(vault), _tool_chunks([masked[:7], masked[7:20], masked[20:]]))

    payloads = [p for p in sse_payloads(out) if isinstance(p, dict)]
    call = _tool_calls(out)[0]
    assert call["id"] == "call_1"
    assert call["name"] == "buscar"
    assert json.loads(call["arguments"]) == restore_strings(json.loads(masked), vault)
    assert json.loads(call["arguments"])["dni"] == DNI
    # The arguments come in one delta, in the chunk that finishes the choice.
    finishing = [p for p in payloads if p["choices"][0].get("finish_reason")]
    assert finishing[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"]
    # No chunk before carries a piece of the (masked) arguments.
    before = [p for p in payloads if not p["choices"][0].get("finish_reason")]
    assert all("ES_DNI" not in json.dumps(p) for p in before)


def test_openai_invalid_json_arguments_are_restored_as_text() -> None:
    out = _run(OpenAIChatStream(_vault()), _tool_chunks(["no es json [[ES_DNI_1]]"]))
    assert _tool_calls(out)[0]["arguments"] == f"no es json {DNI}"


def test_openai_legacy_function_call_arguments() -> None:
    text = (
        _chunk({"function_call": {"name": "f", "arguments": '{"a": "[[ES_'}})
        + _chunk({"function_call": {"arguments": 'DNI_1]]"}'}})
        + _chunk({}, "function_call")
    )
    out = _run(OpenAIChatStream(_vault()), text + "data: [DONE]\n\n")
    arguments = "".join(
        choice["delta"].get("function_call", {}).get("arguments", "")
        for p in sse_payloads(out)
        if isinstance(p, dict)
        for choice in p["choices"]
    )
    assert json.loads(arguments) == {"a": DNI}


def test_openai_two_choices_are_restored_apart() -> None:
    text = openai_sse(["A [[ES_", "DNI_1]]"], done=False, choice=0) + openai_sse(
        ["B [[EMAIL", "_1]]"], choice=1
    )
    out = _run(OpenAIChatStream(_vault()), text)
    by_choice: dict[int, str] = {}
    for payload in sse_payloads(out):
        if isinstance(payload, dict):
            for choice in payload["choices"]:
                content = choice.get("delta", {}).get("content")
                if content:
                    by_choice[choice["index"]] = by_choice.get(choice["index"], "") + content
    assert by_choice == {0: f"A {DNI}", 1: f"B {EMAIL}"}


def test_openai_done_flushes_a_choice_that_never_finished() -> None:
    head = {"id": "c", "object": "chat.completion.chunk", "model": "m"}
    event = {**head, "choices": [{"index": 0, "delta": {"content": "hola [[ES_DNI_1"}}]}
    out = _run(OpenAIChatStream(_vault()), f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n")
    assert openai_text(out) == "hola [[ES_DNI_1"  # unfinished: a placeholder, shown as it is
    flushed = [p for p in sse_payloads(out) if isinstance(p, dict)][-1]
    assert flushed["id"] == "c"
    assert flushed["model"] == "m"


def test_openai_unknown_events_and_fields_pass_and_are_counted() -> None:
    head = {"id": "c", "object": "chat.completion.chunk"}
    odd = {**head, "choices": [{"index": 0, "delta": {"audio": "[[ES_DNI_1]]", "content": "x"}}]}
    text = (
        "data: esto no es json\n\n"
        "event: raro\ndata: {}\n\n"
        ": comentario\n\n"
        f"data: {json.dumps(odd)}\n\n"
        "data: [DONE]\n\n"
    )
    stream = OpenAIChatStream(_vault())
    out = _run(stream, text)
    assert "data: esto no es json\n\n" in out
    assert "event: raro\ndata: {}\n\n" in out
    assert ": comentario\n\n" in out
    assert '"audio": "[[ES_DNI_1]]"' in out  # unknown field: untouched, never restored
    assert stream.unknown == 3


def test_openai_error_event_passes_untouched() -> None:
    error = 'data: {"error": {"message": "overloaded"}}\n\n'
    stream = OpenAIChatStream(_vault())
    assert _run(stream, error, end=False) == error
    assert stream.unknown == 0


def test_openai_stream_without_done_is_cut() -> None:
    stream = OpenAIChatStream(_vault())
    _run(stream, openai_sse(["hola"], done=False), end=False)
    with pytest.raises(StreamCut):
        stream.end()


def test_openai_abort_flushes_safe_text_and_drops_unfinished_arguments() -> None:
    head = {"id": "c", "object": "chat.completion.chunk"}
    deltas = [
        {"content": "texto [[ES_DNI_1]] y [[EMA"},
        {"tool_calls": [{"index": 0, "id": "x", "function": {"name": "f", "arguments": '{"a'}}]},
    ]
    text = "".join(
        f"data: {json.dumps({**head, 'choices': [{'index': 0, 'delta': d}]})}\n\n" for d in deltas
    )
    stream = OpenAIChatStream(_vault())
    out = _run(stream, text, end=False) + stream.abort("upstream_timeout", "fixed message")
    assert openai_text(out) == f"texto {DNI} y [[EMA"
    payloads = sse_payloads(out)
    assert payloads[-1] == {
        "error": {"message": "fixed message", "type": "antifaz_error", "code": "upstream_timeout"}
    }
    assert _tool_calls(out)[0]["arguments"] == ""  # half arguments are never emitted


def test_openai_accumulated_arguments_have_a_limit() -> None:
    stream = OpenAIChatStream(_vault())
    with pytest.raises(StreamLimitExceeded):
        _run(stream, _tool_chunks(_BIG_PIECES), end=False)


# --- Anthropic Messages -------------------------------------------------------------------------


def test_anthropic_text_is_restored_across_deltas() -> None:
    stream = AnthropicMessagesStream(_vault())
    out = _run(stream, anthropic_sse(["Hola [", "[es_dni", "_1 ]] y [[EMAIL_1]", "]."]))
    assert anthropic_text(out) == f"Hola {DNI} y {EMAIL}."
    types = [p["type"] for p in sse_payloads(out)]
    assert types[-1] == "message_stop"
    assert types.index("content_block_stop") > max(
        i for i, t in enumerate(types) if t == "content_block_delta"
    )
    assert stream.unknown == 0


def _block(index: int, block: dict[str, Any], deltas: list[dict[str, Any]]) -> str:
    start = {"type": "content_block_start", "index": index, "content_block": block}
    events = [anthropic_event(start)]
    events += [
        anthropic_event({"type": "content_block_delta", "index": index, "delta": d}) for d in deltas
    ]
    events.append(anthropic_event({"type": "content_block_stop", "index": index}))
    return "".join(events)


def _message(*blocks: str) -> str:
    start = anthropic_event({"type": "message_start", "message": {"id": "m", "content": []}})
    return start + "".join(blocks) + anthropic_event({"type": "message_stop"})


def test_anthropic_tool_input_is_emitted_restored_at_block_stop() -> None:
    vault = _vault()
    masked = json.dumps({"dni": "[[ES_DNI_1]]", "q": 'dijo "hola" \\ [[EMAIL_1]]'})
    deltas = [
        {"type": "input_json_delta", "partial_json": masked[i : i + 5]}
        for i in range(0, len(masked), 5)
    ]
    tool = {"type": "tool_use", "id": "toolu_1", "name": "buscar", "input": {}}
    out = _run(AnthropicMessagesStream(vault), _message(_block(0, tool, deltas)))

    payloads = sse_payloads(out)
    json_deltas = [
        p["delta"]["partial_json"]
        for p in payloads
        if p["type"] == "content_block_delta" and p["delta"]["type"] == "input_json_delta"
    ]
    assert len(json_deltas) == 1  # one delta, at the end of the block
    assert json.loads(json_deltas[0]) == restore_strings(json.loads(masked), vault)
    stop = next(i for i, p in enumerate(payloads) if p["type"] == "content_block_stop")
    assert payloads[stop - 1]["delta"]["type"] == "input_json_delta"


def test_anthropic_thinking_and_signature_pass_byte_for_byte() -> None:
    thinking = _block(
        0,
        {"type": "thinking", "thinking": ""},
        [
            {"type": "thinking_delta", "thinking": "Veo [[ES_DNI_1]] y [[!"},
            {"type": "signature_delta", "signature": SIGNATURE},
        ],
    )
    out = _run(AnthropicMessagesStream(_vault()), _message(thinking))
    assert thinking in out  # every event of the block, byte for byte
    assert DNI not in out


def test_anthropic_redacted_thinking_block_passes_untouched() -> None:
    block = anthropic_event(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "redacted_thinking", "data": "RW5jcnlwdGVk[[ES_DNI_1]]"},
        }
    )
    out = _run(AnthropicMessagesStream(_vault()), _message(block))
    assert block in out


def test_anthropic_text_in_block_start_is_restored() -> None:
    out = _run(
        AnthropicMessagesStream(_vault()),
        _message(_block(0, {"type": "text", "text": "Ya: [[ES_DNI_1]]"}, [])),
    )
    starts = [p for p in sse_payloads(out) if p["type"] == "content_block_start"]
    assert starts[0]["content_block"]["text"] == f"Ya: {DNI}"


def test_anthropic_ping_error_and_unknown_events_pass() -> None:
    text = (
        anthropic_event({"type": "ping"})
        + 'event: futuro\ndata: {"type": "futuro", "t": "[[ES_DNI_1]]"}\n\n'
        + anthropic_event(
            {"type": "content_block_delta", "index": 0, "delta": {"type": "citations_delta"}}
        )
        + "data: no json\n\n"
        + anthropic_event({"type": "error", "error": {"type": "overloaded_error", "message": "x"}})
    )
    stream = AnthropicMessagesStream(_vault())
    out = _run(stream, text, end=False)
    assert out == text
    assert stream.unknown == 3


def test_anthropic_stream_without_message_stop_is_cut() -> None:
    stream = AnthropicMessagesStream(_vault())
    _run(stream, anthropic_sse(["hola"], done=False), end=False)
    with pytest.raises(StreamCut):
        stream.end()


def test_anthropic_abort_flushes_text_drops_tool_input_and_ends_with_error() -> None:
    tool = {"type": "tool_use", "id": "t", "name": "f", "input": {}}
    text = (
        anthropic_event(TEXT_BLOCK_START)
        + anthropic_event({"type": "content_block_start", "index": 1, "content_block": tool})
        + anthropic_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hola [[ES_DNI_1]] [[ES"},
            }
        )
        + anthropic_event(
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"a": "[[ES_DNI_1]]'},
            }
        )
    )
    stream = AnthropicMessagesStream(_vault())
    out = _run(stream, text, end=False) + stream.abort("upstream_timeout", "fixed message")
    assert anthropic_text(out) == f"Hola {DNI} [[ES"
    assert "input_json_delta" not in out
    assert sse_payloads(out)[-1] == {
        "type": "error",
        "error": {"type": "api_error", "message": "fixed message"},
    }
    assert out.endswith("\n\n") and "event: error\n" in out


def test_anthropic_accumulated_input_has_a_limit() -> None:
    deltas = [{"type": "input_json_delta", "partial_json": big} for big in _BIG_PIECES]
    with pytest.raises(StreamLimitExceeded):
        _run(AnthropicMessagesStream(_vault()), _block(0, {"type": "tool_use"}, deltas), end=False)


# --- Review round -------------------------------------------------------------------------------


def test_openai_limit_in_one_choice_keeps_what_other_choices_restored() -> None:
    stream = OpenAIChatStream(_vault())
    choices = [
        {"index": 0, "delta": {"content": "hola [[ES_DNI_1]] y [[EMA"}},
        {"index": 1, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "x"}}]}},
    ]
    stream._arguments._size = MAX_ACCUMULATED_CHARS  # the next piece passes the limit
    event = SSEParser().feed(f"data: {json.dumps({'id': 'c', 'choices': choices})}\n\n")[0]
    with pytest.raises(StreamLimitExceeded):
        stream.event(event)
    out = stream.abort("stream_limit_exceeded", "fixed")
    assert openai_text(out) == f"hola {DNI} y [[EMA"  # restored text is never dropped
    assert "arguments" not in out


def test_openai_tool_call_without_index_passes_and_is_counted() -> None:
    call = {"id": "x", "function": {"name": "f", "arguments": '{"a": "[[ES_DNI_1]]"}'}}
    chunk = _chunk({"tool_calls": [call]})
    stream = OpenAIChatStream(_vault())
    assert _run(stream, chunk, end=False) == chunk
    assert stream.unknown == 1


def test_openai_empty_delta_with_extra_top_level_field_is_kept() -> None:
    chunk = {"id": "c", "obfuscation": "abc", "choices": [{"index": 0, "delta": {"content": "[["}}]}
    out = _run(OpenAIChatStream(_vault()), f"data: {json.dumps(chunk)}\n\n", end=False)
    (payload,) = sse_payloads(out)
    assert payload["obfuscation"] == "abc"
    assert payload["choices"][0]["delta"]["content"] == ""


def test_openai_provider_error_event_ends_the_stream_without_a_cut() -> None:
    stream = OpenAIChatStream(_vault())
    _run(stream, 'data: {"error": {"message": "overloaded"}}\n\n', end=False)
    assert stream.end() == ""


def test_anthropic_provider_error_event_ends_the_stream_without_a_cut() -> None:
    stream = AnthropicMessagesStream(_vault())
    error = anthropic_event({"type": "error", "error": {"type": "overloaded_error"}})
    _run(stream, error, end=False)
    assert stream.end() == ""


@pytest.mark.parametrize("block", [{"type": "server_tool_use", "id": "s", "name": "web"}, None])
def test_anthropic_input_json_of_other_blocks_passes_untouched(
    block: dict[str, Any] | None,
) -> None:
    delta = anthropic_event(
        {
            "type": "content_block_delta",
            "index": 3,
            "delta": {"type": "input_json_delta", "partial_json": '{"q": "[[ES_DNI_1]]"}'},
        }
    )
    start = (
        anthropic_event({"type": "content_block_start", "index": 3, "content_block": block})
        if block
        else ""
    )
    stream = AnthropicMessagesStream(_vault())
    out = _run(stream, start + delta, end=False)
    assert delta in out
    assert DNI not in out
    assert stream.unknown == 1


# --- Malformed indexes: the stream ends (fail closed) -----------------------------------------

BAD_INDEXES = [0.0, "0", True, -1, None, [0]]


@pytest.mark.parametrize("index", BAD_INDEXES)
def test_openai_choice_with_a_malformed_index_is_a_malformed_stream(index: object) -> None:
    chunk = {"object": "chat.completion.chunk", "choices": [{"index": index, "delta": {}}]}
    stream = OpenAIChatStream(_vault())
    with pytest.raises(MalformedStream):
        _run(stream, f"data: {json.dumps(chunk)}\n\n", end=False)


@pytest.mark.parametrize("choice", [{"delta": {"content": "x"}}, "x", 0])
def test_openai_choice_without_index_is_a_malformed_stream(choice: object) -> None:
    chunk = {"object": "chat.completion.chunk", "choices": [choice]}
    stream = OpenAIChatStream(_vault())
    with pytest.raises(MalformedStream):
        _run(stream, f"data: {json.dumps(chunk)}\n\n", end=False)


@pytest.mark.parametrize("index", BAD_INDEXES)
def test_openai_tool_call_with_a_malformed_index_is_a_malformed_stream(index: object) -> None:
    call = {"index": index, "function": {"arguments": "{}"}}
    stream = OpenAIChatStream(_vault())
    with pytest.raises(MalformedStream):
        _run(stream, _chunk({"tool_calls": [call]}), end=False)


@pytest.mark.parametrize("index", BAD_INDEXES)
@pytest.mark.parametrize("kind", ["content_block_start", "content_block_delta", "ping"])
def test_anthropic_event_with_a_malformed_index_is_a_malformed_stream(
    kind: str, index: object
) -> None:
    payload = {"type": kind, "index": index, "delta": {"type": "text_delta", "text": "x"}}
    stream = AnthropicMessagesStream(_vault())
    with pytest.raises(MalformedStream):
        _run(stream, anthropic_event(payload), end=False)


@pytest.mark.parametrize(
    "kind", ["content_block_start", "content_block_delta", "content_block_stop"]
)
def test_anthropic_content_block_without_index_is_a_malformed_stream(kind: str) -> None:
    payload = {"type": kind, "delta": {"type": "text_delta", "text": "x"}}
    stream = AnthropicMessagesStream(_vault())
    with pytest.raises(MalformedStream):
        _run(stream, anthropic_event(payload), end=False)


# --- KeyWatch (invariant 13 in a stream) --------------------------------------------------------

KEY = "sk-test-not-a-real-key-0123456789"  # fake


def _openai_event(*choices: dict[str, Any], **extra: Any) -> str:
    chunk = {"object": "chat.completion.chunk", "choices": list(choices), **extra}
    return f"data: {json.dumps(chunk)}\n\n"


def test_keywatch_catches_a_key_split_between_two_deltas_of_one_choice() -> None:
    watch = KeyWatch([KEY])
    watch.check_output(_openai_event({"index": 0, "delta": {"content": KEY[:10]}}))
    with pytest.raises(KeyEchoed):
        watch.check_output(_openai_event({"index": 0, "delta": {"content": KEY[10:]}}))


def test_keywatch_too_many_fields_ends_the_stream_instead_of_forgetting() -> None:
    watch = KeyWatch([KEY])
    watch.check_output(_openai_event({"index": 0, "delta": {"content": KEY[:10]}}))
    filler = {f"x{i}": "a" for i in range(5000)}
    with pytest.raises(StreamLimitExceeded):
        watch.check_output(_openai_event({"index": 1, "delta": {}}, relleno=filler))


def test_keywatch_a_few_thousand_fields_in_total_still_pass() -> None:
    watch = KeyWatch([KEY])
    for start in range(0, 3000, 1000):
        filler = {f"x{i}": "a" for i in range(start, start + 1000)}
        watch.check_output(_openai_event({"index": 0, "delta": {}}, relleno=filler))


def test_keywatch_keeps_one_tail_per_field_name_across_paths() -> None:
    """The same field name in another path (another choice, an odd nesting) still joins."""
    watch = KeyWatch([KEY])
    watch.check_output(_openai_event({"index": 0, "delta": {"content": KEY[:10]}}))
    with pytest.raises(KeyEchoed):
        watch.check_output(f"data: {json.dumps({'otro': [{'content': KEY[10:]}]})}\n\n")


@pytest.mark.parametrize("name", ["text", "partial_json", "arguments", "refusal", "thinking"])
def test_keywatch_name_tail_works_for_any_string_field(name: str) -> None:
    watch = KeyWatch([KEY])
    watch.check_output(f"data: {json.dumps({'a': {name: KEY[:7]}})}\n\n")
    with pytest.raises(KeyEchoed):
        watch.check_output(f"data: {json.dumps({'b': [{name: KEY[7:]}]})}\n\n")


def test_keywatch_unrelated_text_is_not_a_key() -> None:
    watch = KeyWatch([KEY])
    watch.check_output(_openai_event({"index": 0, "delta": {"content": KEY[:10]}}))
    watch.check_output(_openai_event({"index": 1, "delta": {"content": "hola"}}))
    watch.check_output(_openai_event({"index": 0, "delta": {"content": "adios"}}))
