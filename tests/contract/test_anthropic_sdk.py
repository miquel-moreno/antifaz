"""The official Anthropic SDK against Antifaz: it parses every answer and gets the values back.

Behind Antifaz, a fake provider serves hand-written fixtures (tests/contract/fixtures). No
network, no real key.
"""

import json
from typing import Any

import anthropic
import pytest

from tests.contract.conftest import (
    DNI,
    EMAIL,
    RESTORED_GREETING,
    RESTORED_TOOL_INPUT,
    USER_TEXT,
    FixtureUpstream,
    assert_nothing_leaked,
    load_json,
)

MODEL = "claude-sonnet-4-5"
SIGNATURE = load_json("anthropic_messages_thinking.json")["body"]["content"][0]["signature"]
MASKED_THINKING = "El usuario da [[ES_DNI_1]]; lo confirmo sin inventar nada."
EARLIER_THINKING = "El usuario saluda; respondo breve."
TOOLS: list[Any] = [
    {
        "name": "buscar_cliente",
        "description": "Busca un cliente por sus datos",
        "input_schema": {
            "type": "object",
            "properties": {
                "dni": {"type": "string"},
                "email": {"type": "string"},
                "iban": {"type": "string"},
            },
            "required": ["dni"],
        },
    }
]
# The data in the system prompt, the user text, an earlier tool_use input and a tool_result.
TOOL_HISTORY: list[Any] = [
    {"role": "user", "content": [{"type": "text", "text": USER_TEXT}]},
    {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_prev_0001",
                "name": "buscar_cliente",
                "input": {"dni": DNI},
            },
        ],
    },
    {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_prev_0001",
                "content": [{"type": "text", "text": f"Cliente {DNI}: {EMAIL}"}],
            },
        ],
    },
]
SYSTEM = [{"type": "text", "text": f"El cliente actual tiene el DNI {DNI}."}]


def thinking_history(earlier_thinking: str = EARLIER_THINKING) -> list[Any]:
    """An earlier assistant turn with reasoning, which must reach the provider untouched
    (invariant 9), and then the user text."""
    return [
        {"role": "user", "content": "Hola"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": earlier_thinking, "signature": SIGNATURE},
                {"type": "text", "text": "Hola, dime."},
            ],
        },
        {"role": "user", "content": USER_TEXT},
    ]


async def test_message_text_is_parsed_and_restored(
    anthropic_client: anthropic.AsyncAnthropic, upstream: FixtureUpstream
) -> None:
    upstream.serve("anthropic_messages_text.json")

    message = await anthropic_client.messages.create(
        model=MODEL,
        max_tokens=256,
        system="Eres un asistente.",
        messages=[{"role": "user", "content": USER_TEXT}],
    )

    assert message.content[0].type == "text"
    assert message.content[0].text == RESTORED_GREETING
    assert message.stop_reason == "end_turn"
    assert message.usage.output_tokens == 20
    request = upstream.requests[0]
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert "[[ES_DNI_1]]" in upstream.bodies()[0]["messages"][0]["content"]
    assert_nothing_leaked(upstream)


async def test_tool_use_input_is_restored(
    anthropic_client: anthropic.AsyncAnthropic, upstream: FixtureUpstream
) -> None:
    upstream.serve("anthropic_messages_tool_use.json")

    message = await anthropic_client.messages.create(
        model=MODEL, max_tokens=256, system=SYSTEM, messages=TOOL_HISTORY, tools=TOOLS
    )

    text, tool_use = message.content
    assert text.type == "text" and text.text == f"Busco al cliente {DNI}."
    assert tool_use.type == "tool_use"
    assert tool_use.name == "buscar_cliente"
    assert tool_use.input == RESTORED_TOOL_INPUT
    assert message.stop_reason == "tool_use"
    assert_nothing_leaked(upstream)


async def test_thinking_is_untouched_and_text_restored(
    anthropic_client: anthropic.AsyncAnthropic, upstream: FixtureUpstream
) -> None:
    upstream.serve("anthropic_messages_thinking.json")

    message = await anthropic_client.messages.create(
        model=MODEL,
        max_tokens=2048,
        thinking={"type": "enabled", "budget_tokens": 1024},
        messages=thinking_history(),
    )

    thinking, text = message.content
    assert thinking.type == "thinking"
    assert thinking.thinking == MASKED_THINKING  # reasoning is never restored (invariant 9)
    assert thinking.signature == SIGNATURE
    assert text.type == "text" and text.text == f"Recibido: {DNI}."
    sent_block = upstream.bodies()[0]["messages"][1]["content"][0]
    assert sent_block == thinking_history()[1]["content"][0]
    assert_nothing_leaked(upstream)


async def test_placeholder_of_earlier_reasoning_is_never_restored_to_a_new_value(
    anthropic_client: anthropic.AsyncAnthropic, upstream: FixtureUpstream
) -> None:
    """Earlier reasoning already holds [[ES_DNI_1]]: the new DNI gets [[ES_DNI_2]], and an
    [[ES_DNI_1]] in the answer stays a placeholder (it belongs to another turn)."""
    upstream.serve("anthropic_messages_thinking.json")

    message = await anthropic_client.messages.create(
        model=MODEL,
        max_tokens=2048,
        thinking={"type": "enabled", "budget_tokens": 1024},
        messages=thinking_history(MASKED_THINKING),
    )

    assert message.content[1].type == "text"
    assert message.content[1].text == "Recibido: [[ES_DNI_1]]."
    assert "[[ES_DNI_2]]" in upstream.bodies()[0]["messages"][2]["content"]
    assert_nothing_leaked(upstream)


async def test_stream_text_deltas_are_restored(
    anthropic_client: anthropic.AsyncAnthropic, upstream: FixtureUpstream
) -> None:
    upstream.serve("anthropic_messages_stream_text.sse")

    async with anthropic_client.messages.stream(
        model=MODEL, max_tokens=256, messages=[{"role": "user", "content": USER_TEXT}]
    ) as stream:
        text = "".join([piece async for piece in stream.text_stream])
        final = await stream.get_final_message()

    assert text == RESTORED_GREETING
    assert final.content[0].type == "text" and final.content[0].text == RESTORED_GREETING
    assert final.stop_reason == "end_turn"
    assert upstream.bodies()[0]["stream"] is True
    assert_nothing_leaked(upstream)


async def test_stream_input_json_delta_is_restored(
    anthropic_client: anthropic.AsyncAnthropic, upstream: FixtureUpstream
) -> None:
    upstream.serve("anthropic_messages_stream_tool_use.sse")

    partial_json = []
    async with anthropic_client.messages.stream(
        model=MODEL, max_tokens=256, system=SYSTEM, messages=TOOL_HISTORY, tools=TOOLS
    ) as stream:
        async for event in stream:
            if event.type == "content_block_delta" and event.delta.type == "input_json_delta":
                partial_json.append(event.delta.partial_json)
        final = await stream.get_final_message()

    assert json.loads("".join(partial_json)) == RESTORED_TOOL_INPUT
    text, tool_use = final.content
    assert text.type == "text" and text.text == f"Busco al cliente {DNI}."
    assert tool_use.type == "tool_use" and tool_use.input == RESTORED_TOOL_INPUT
    assert final.stop_reason == "tool_use"
    assert_nothing_leaked(upstream)


async def test_stream_thinking_and_signature_are_untouched(
    anthropic_client: anthropic.AsyncAnthropic, upstream: FixtureUpstream
) -> None:
    upstream.serve("anthropic_messages_stream_thinking.sse")

    async with anthropic_client.messages.stream(
        model=MODEL,
        max_tokens=2048,
        thinking={"type": "enabled", "budget_tokens": 1024},
        messages=thinking_history(),
    ) as stream:
        final = await stream.get_final_message()

    thinking, text = final.content
    assert thinking.type == "thinking"
    assert thinking.thinking == MASKED_THINKING
    assert thinking.signature == SIGNATURE
    assert text.type == "text" and text.text == f"Recibido: {DNI}."
    assert_nothing_leaked(upstream)


async def test_count_tokens_is_masked_and_parsed(
    anthropic_client: anthropic.AsyncAnthropic, upstream: FixtureUpstream
) -> None:
    upstream.serve("anthropic_count_tokens.json")

    count = await anthropic_client.messages.count_tokens(
        model=MODEL, system=SYSTEM, messages=TOOL_HISTORY, tools=TOOLS
    )

    assert count.input_tokens == 42
    assert upstream.requests[0].url.path == "/v1/messages/count_tokens"
    assert_nothing_leaked(upstream)


@pytest.mark.parametrize(
    ("fixture", "error"),
    [
        ("anthropic_error_401.json", anthropic.AuthenticationError),
        ("anthropic_error_400.json", anthropic.BadRequestError),
        ("anthropic_error_429.json", anthropic.RateLimitError),
        ("anthropic_error_502.json", anthropic.InternalServerError),
    ],
)
@pytest.mark.parametrize("stream", [False, True])
async def test_provider_errors_raise_the_sdk_exception(
    anthropic_client: anthropic.AsyncAnthropic,
    upstream: FixtureUpstream,
    fixture: str,
    error: type[anthropic.APIStatusError],
    stream: bool,
) -> None:
    upstream.serve(fixture)

    with pytest.raises(error) as info:
        if stream:
            async with anthropic_client.messages.stream(
                model=MODEL, max_tokens=64, messages=[{"role": "user", "content": USER_TEXT}]
            ) as events:
                await events.get_final_message()
        else:
            await anthropic_client.messages.create(
                model=MODEL, max_tokens=64, messages=[{"role": "user", "content": USER_TEXT}]
            )

    assert info.value.status_code == int(fixture[-8:-5])
    assert DNI not in str(info.value.body) + info.value.message
    assert_nothing_leaked(upstream)


async def test_count_tokens_error_raises_the_sdk_exception(
    anthropic_client: anthropic.AsyncAnthropic, upstream: FixtureUpstream
) -> None:
    upstream.serve("anthropic_error_429.json")

    with pytest.raises(anthropic.RateLimitError):
        await anthropic_client.messages.count_tokens(
            model=MODEL, messages=[{"role": "user", "content": USER_TEXT}]
        )

    assert_nothing_leaked(upstream)


async def test_wrong_antifaz_key_raises_authentication_error(
    anthropic_client: anthropic.AsyncAnthropic, upstream: FixtureUpstream
) -> None:
    upstream.serve("anthropic_messages_text.json")

    with pytest.raises(anthropic.AuthenticationError):
        await anthropic_client.with_options(api_key="wrong-key").messages.create(
            model=MODEL, max_tokens=64, messages=[{"role": "user", "content": USER_TEXT}]
        )

    assert upstream.requests == []


async def test_image_is_blocked_with_bad_request_error(
    anthropic_client: anthropic.AsyncAnthropic, upstream: FixtureUpstream
) -> None:
    upstream.serve("anthropic_messages_text.json")
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": "iVBORw0KGgo="}}  # fmt: skip

    with pytest.raises(anthropic.BadRequestError):
        await anthropic_client.messages.create(
            model=MODEL,
            max_tokens=64,
            messages=[{"role": "user", "content": [{"type": "text", "text": USER_TEXT}, image]}],
        )

    assert upstream.requests == []
