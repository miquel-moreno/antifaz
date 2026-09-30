"""The official OpenAI SDK against Antifaz: it parses every answer and gets the values back.

Behind Antifaz, a fake provider serves hand-written fixtures (tests/contract/fixtures). No
network, no real key.
"""

import json
from typing import Any

import openai
import pytest

from tests.contract.conftest import (
    DNI,
    EMAIL,
    RESTORED_GREETING,
    RESTORED_TOOL_INPUT,
    USER_TEXT,
    FixtureUpstream,
    assert_nothing_leaked,
)

MODEL = "gpt-4o-mini"
TOOLS: list[Any] = [
    {
        "type": "function",
        "function": {
            "name": "buscar_cliente",
            "description": "Busca un cliente por sus datos",
            "parameters": {
                "type": "object",
                "properties": {
                    "dni": {"type": "string"},
                    "email": {"type": "string"},
                    "iban": {"type": "string"},
                },
                "required": ["dni"],
            },
        },
    }
]
# A conversation with the data in the system prompt, the user text, earlier tool arguments and
# a tool result: every place the SDK can put text.
TOOL_HISTORY: list[Any] = [
    {"role": "system", "content": f"El cliente actual tiene el DNI {DNI}."},
    {"role": "user", "content": [{"type": "text", "text": USER_TEXT}]},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_prev_0001",
                "type": "function",
                "function": {"name": "buscar_cliente", "arguments": json.dumps({"dni": DNI})},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_prev_0001", "content": f"Cliente {DNI}: {EMAIL}"},
]


async def test_chat_completion_text_is_parsed_and_restored(
    openai_client: openai.AsyncOpenAI, upstream: FixtureUpstream
) -> None:
    upstream.serve("openai_chat_text.json")

    completion = await openai_client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "Eres un asistente."},
            {"role": "user", "content": USER_TEXT},
        ],
    )

    assert completion.choices[0].message.content == RESTORED_GREETING
    assert completion.choices[0].finish_reason == "stop"
    assert completion.usage is not None and completion.usage.total_tokens == 49
    assert "[[ES_DNI_1]]" in upstream.bodies()[0]["messages"][1]["content"]
    assert_nothing_leaked(upstream)


async def test_chat_completion_tool_call_arguments_are_restored_json(
    openai_client: openai.AsyncOpenAI, upstream: FixtureUpstream
) -> None:
    upstream.serve("openai_chat_tool_call.json")

    completion = await openai_client.chat.completions.create(
        model=MODEL, messages=TOOL_HISTORY, tools=TOOLS
    )

    call = completion.choices[0].message.tool_calls[0]  # type: ignore[index]
    assert call.function.name == "buscar_cliente"  # type: ignore[union-attr]
    assert json.loads(call.function.arguments) == RESTORED_TOOL_INPUT  # type: ignore[union-attr]
    assert completion.choices[0].finish_reason == "tool_calls"
    assert_nothing_leaked(upstream)


async def test_streamed_text_is_parsed_and_restored(
    openai_client: openai.AsyncOpenAI, upstream: FixtureUpstream
) -> None:
    upstream.serve("openai_chat_stream_text.sse")

    stream = await openai_client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": USER_TEXT}],
        stream=True,
        stream_options={"include_usage": True},
    )
    text, finish, usage = [], None, None
    async for chunk in stream:
        for choice in chunk.choices:
            text.append(choice.delta.content or "")
            finish = choice.finish_reason or finish
        usage = chunk.usage or usage

    assert "".join(text) == RESTORED_GREETING
    assert finish == "stop"
    assert usage is not None and usage.total_tokens == 49
    assert upstream.bodies()[0]["stream"] is True
    assert_nothing_leaked(upstream)


async def test_streamed_tool_call_arguments_are_restored_json(
    openai_client: openai.AsyncOpenAI, upstream: FixtureUpstream
) -> None:
    upstream.serve("openai_chat_stream_tool_call.sse")

    stream = await openai_client.chat.completions.create(
        model=MODEL, messages=TOOL_HISTORY, tools=TOOLS, stream=True
    )
    names, arguments, finish = [], [], None
    async for chunk in stream:
        for choice in chunk.choices:
            for call in choice.delta.tool_calls or []:
                if call.function and call.function.name:
                    names.append(call.function.name)
                if call.function and call.function.arguments:
                    arguments.append(call.function.arguments)
            finish = choice.finish_reason or finish

    assert names == ["buscar_cliente"]
    assert json.loads("".join(arguments)) == RESTORED_TOOL_INPUT
    assert finish == "tool_calls"
    assert_nothing_leaked(upstream)


async def test_stream_helper_accumulates_the_restored_tool_call(
    openai_client: openai.AsyncOpenAI, upstream: FixtureUpstream
) -> None:
    """The SDK's own stream helper rebuilds the final completion from Antifaz's events."""
    upstream.serve("openai_chat_stream_tool_call.sse")

    async with openai_client.chat.completions.stream(
        model=MODEL, messages=TOOL_HISTORY, tools=TOOLS
    ) as stream:
        final = await stream.get_final_completion()

    call = final.choices[0].message.tool_calls[0]  # type: ignore[index]
    assert json.loads(call.function.arguments) == RESTORED_TOOL_INPUT  # type: ignore[union-attr]
    assert_nothing_leaked(upstream)


@pytest.mark.parametrize(
    ("fixture", "error"),
    [
        ("openai_error_401.json", openai.AuthenticationError),
        ("openai_error_400.json", openai.BadRequestError),
        ("openai_error_429.json", openai.RateLimitError),
        ("openai_error_502.json", openai.InternalServerError),
    ],
)
@pytest.mark.parametrize("stream", [False, True])
async def test_provider_errors_raise_the_sdk_exception(
    openai_client: openai.AsyncOpenAI,
    upstream: FixtureUpstream,
    fixture: str,
    error: type[openai.APIStatusError],
    stream: bool,
) -> None:
    upstream.serve(fixture)

    with pytest.raises(error) as info:
        await openai_client.chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": USER_TEXT}], stream=stream
        )

    assert info.value.status_code == int(fixture[-8:-5])
    assert DNI not in str(info.value.body) + info.value.message
    assert_nothing_leaked(upstream)


async def test_wrong_antifaz_key_raises_authentication_error(
    openai_client: openai.AsyncOpenAI, upstream: FixtureUpstream
) -> None:
    upstream.serve("openai_chat_text.json")

    with pytest.raises(openai.AuthenticationError):
        await openai_client.with_options(api_key="wrong-key").chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": USER_TEXT}]
        )

    assert upstream.requests == []


async def test_attachment_is_blocked_with_bad_request_error(
    openai_client: openai.AsyncOpenAI, upstream: FixtureUpstream
) -> None:
    upstream.serve("openai_chat_text.json")
    image = {"type": "image_url", "image_url": {"url": "https://example.com/dni.png"}}

    with pytest.raises(openai.BadRequestError) as info:
        await openai_client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": [{"type": "text", "text": USER_TEXT}, image]}],
        )

    assert info.value.code == "antifaz_blocked"
    assert upstream.requests == []


# Real answers from gpt-4.1-nano, recorded through Antifaz on 2026-09-30 and sanitised (see
# fixtures/README.md). The provider split every placeholder across several stream chunks
# (" [[", "ES", "_D", "NI", "_", "1", "]]"). A forced tool choice ends with "stop".
RECORDED_TEXT = [
    {"role": "user", "content": f"Repite esta frase: Mi DNI es {DNI} y mi correo es {EMAIL}."}
]
RECORDED_LOOKUP = [{"role": "user", "content": f"Busca el cliente con DNI {DNI}."}]
RECORDED_TOOLS: list[Any] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_customer",
            "description": "Look up a customer by DNI",
            "parameters": {
                "type": "object",
                "properties": {"dni": {"type": "string"}},
                "required": ["dni"],
            },
        },
    }
]
RECORDED_GREETING = f"Mi DNI es {DNI} y mi correo es {EMAIL}."


async def test_recorded_chat_completion_is_restored(
    openai_client: openai.AsyncOpenAI, upstream: FixtureUpstream
) -> None:
    upstream.serve("openai_recorded_chat_text.json")

    completion = await openai_client.chat.completions.create(
        model="gpt-4.1-nano", messages=RECORDED_TEXT
    )

    assert completion.choices[0].message.content == RECORDED_GREETING
    assert completion.usage is not None and completion.usage.total_tokens == 55
    assert_nothing_leaked(upstream)


async def test_recorded_stream_with_split_placeholders_is_restored(
    openai_client: openai.AsyncOpenAI, upstream: FixtureUpstream
) -> None:
    upstream.serve("openai_recorded_chat_stream_text.sse")

    stream = await openai_client.chat.completions.create(
        model="gpt-4.1-nano",
        messages=RECORDED_TEXT,
        stream=True,
        stream_options={"include_usage": True},
    )
    text, finish, usage = [], None, None
    async for chunk in stream:
        for choice in chunk.choices:
            text.append(choice.delta.content or "")
            finish = choice.finish_reason or finish
        usage = chunk.usage or usage

    assert "".join(text) == RECORDED_GREETING
    assert finish == "stop"
    assert usage is not None and usage.total_tokens == 55
    assert_nothing_leaked(upstream)


async def test_recorded_tool_call_arguments_are_restored_json(
    openai_client: openai.AsyncOpenAI, upstream: FixtureUpstream
) -> None:
    upstream.serve("openai_recorded_chat_tool_call.json")

    completion = await openai_client.chat.completions.create(
        model="gpt-4.1-nano", messages=RECORDED_LOOKUP, tools=RECORDED_TOOLS
    )

    call = completion.choices[0].message.tool_calls[0]  # type: ignore[index]
    assert call.function.name == "lookup_customer"  # type: ignore[union-attr]
    assert json.loads(call.function.arguments) == {"dni": DNI}  # type: ignore[union-attr]
    assert_nothing_leaked(upstream)


async def test_recorded_streamed_tool_call_arguments_are_restored_json(
    openai_client: openai.AsyncOpenAI, upstream: FixtureUpstream
) -> None:
    upstream.serve("openai_recorded_chat_stream_tool_call.sse")

    stream = await openai_client.chat.completions.create(
        model="gpt-4.1-nano", messages=RECORDED_LOOKUP, tools=RECORDED_TOOLS, stream=True
    )
    names, arguments = [], []
    async for chunk in stream:
        for choice in chunk.choices:
            for call in choice.delta.tool_calls or []:
                if call.function and call.function.name:
                    names.append(call.function.name)
                if call.function and call.function.arguments:
                    arguments.append(call.function.arguments)

    assert names == ["lookup_customer"]
    assert json.loads("".join(arguments)) == {"dni": DNI}
    assert_nothing_leaked(upstream)
