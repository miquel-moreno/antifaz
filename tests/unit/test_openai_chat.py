"""Field walk of the OpenAI Chat format (issue 5a, ADR-0013). Synthetic data only."""

import json
from collections.abc import Sequence

import pytest

from antifaz import DetectorFailed, Span
from antifaz.detect.types import Confidence, EntityType, Layer
from antifaz.errors import AttachmentBlocked, UnmaskableField
from antifaz.providers.openai_chat import mask_request, restore_response
from antifaz.restore import restore

DNI = "12345678Z"
OTHER = "87654321X"


def _dump(node: object) -> str:
    return json.dumps(node, ensure_ascii=False)


def test_string_content_is_masked() -> None:
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": f"DNI {DNI}"}]}

    masked, vault = mask_request(body)

    assert masked["messages"] == [{"role": "user", "content": "DNI [[ES_DNI_1]]"}]
    assert masked["model"] == "gpt-4o-mini"
    assert len(vault) == 1


def test_input_is_not_modified() -> None:
    body = {"messages": [{"role": "user", "content": DNI}]}

    mask_request(body)

    assert body == {"messages": [{"role": "user", "content": DNI}]}


def test_text_parts_are_masked() -> None:
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": f"a {DNI}"}]},
        ]
    }

    masked, _ = mask_request(body)

    assert masked["messages"][0]["content"] == [{"type": "text", "text": "a [[ES_DNI_1]]"}]


def test_same_value_gets_same_placeholder_across_messages() -> None:
    body = {
        "messages": [
            {"role": "system", "content": f"{DNI} y {OTHER}"},
            {"role": "user", "content": f"otra vez {OTHER}"},
            {"role": "tool", "tool_call_id": "call_1", "content": f"{DNI}"},
        ]
    }

    masked, _ = mask_request(body)

    contents = [m["content"] for m in masked["messages"]]
    assert contents == [
        "[[ES_DNI_1]] y [[ES_DNI_2]]",
        "otra vez [[ES_DNI_2]]",
        "[[ES_DNI_1]]",
    ]


def test_name_and_numbers_are_handled() -> None:
    body = {
        "messages": [{"role": "user", "name": f"u {DNI}", "content": "hola"}],
        "temperature": 0.2,
        "max_tokens": 10,
        "n": 1,
        "stream": False,
        "logprobs": None,
    }

    masked, _ = mask_request(body)

    assert DNI not in _dump(masked)
    assert masked["temperature"] == 0.2
    assert masked["max_tokens"] == 10
    assert masked["stream"] is False
    assert masked["logprobs"] is None


def test_tool_call_arguments_are_parsed_and_values_masked() -> None:
    arguments = json.dumps({"dni": DNI, "nested": {"list": [f"x {OTHER}", 3, True]}})
    body = {
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "buscar", "arguments": arguments},
                    }
                ],
            }
        ]
    }

    masked, _ = mask_request(body)

    sent = masked["messages"][0]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(sent) == {
        "dni": "[[ES_DNI_1]]",
        "nested": {"list": ["x [[ES_DNI_2]]", 3, True]},
    }


def test_invalid_json_arguments_are_masked_as_text() -> None:
    body = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "f", "arguments": f'{{"dni": "{DNI}"'}}],
            }
        ]
    }

    masked, _ = mask_request(body)

    sent = masked["messages"][0]["tool_calls"][0]["function"]["arguments"]
    assert sent == '{"dni": "[[ES_DNI_1]]"'


def test_tool_schemas_pass_unchanged_without_personal_data() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "buscar_pedido",
                "description": "Busca un pedido",
                "parameters": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            },
        }
    ]
    body = {"messages": [{"role": "user", "content": "hola"}], "tools": tools}

    masked, _ = mask_request(body)

    assert masked["tools"] == tools


def test_unknown_text_field_is_masked_generically() -> None:
    body = {
        "messages": [{"role": "user", "content": "hola"}],
        "campo_nuevo": {"texto": f"DNI {DNI}", "lista": [DNI]},
    }

    masked, _ = mask_request(body)

    assert masked["campo_nuevo"] == {"texto": "DNI [[ES_DNI_1]]", "lista": ["[[ES_DNI_1]]"]}


def test_a_key_with_personal_data_blocks() -> None:
    body = {"messages": [{"role": "user", "content": "hola"}], "metadata": {DNI: "x"}}

    with pytest.raises(UnmaskableField) as info:
        mask_request(body)

    assert DNI not in str(info.value)


def test_a_key_inside_tool_arguments_with_personal_data_blocks() -> None:
    arguments = json.dumps({DNI: 1})
    body = {
        "messages": [{"role": "assistant", "tool_calls": [{"function": {"arguments": arguments}}]}]
    }

    with pytest.raises(UnmaskableField):
        mask_request(body)


@pytest.mark.parametrize(
    "part",
    [
        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
        {"type": "input_audio", "input_audio": {"data": "AAAA", "format": "wav"}},
        {"type": "file", "file": {"file_id": "file-abc"}},
        {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
        {"type": "text", "text": "hola", "file_id": "file-abc"},
    ],
)
def test_attachments_block(part: dict[str, object]) -> None:
    body = {"messages": [{"role": "user", "content": [part]}]}

    with pytest.raises(AttachmentBlocked):
        mask_request(body)


def test_assistant_audio_reference_blocks() -> None:
    body = {"messages": [{"role": "assistant", "audio": {"id": "audio_1"}}]}

    with pytest.raises(AttachmentBlocked):
        mask_request(body)


def test_detector_failure_blocks() -> None:
    def broken(_: str) -> Sequence[Span]:
        raise RuntimeError(DNI)

    with pytest.raises(DetectorFailed):
        mask_request({"messages": [{"role": "user", "content": DNI}]}, detector=broken)


def test_user_brackets_are_escaped() -> None:
    masked, vault = mask_request({"messages": [{"role": "user", "content": "[[ES_DNI_1]]"}]})

    assert masked["messages"][0]["content"] == "[[!ES_DNI_1]]"
    assert restore(masked["messages"][0]["content"], vault) == "[[ES_DNI_1]]"


def _response(message: dict[str, object]) -> dict[str, object]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"total_tokens": 3},
    }


def test_response_content_and_refusal_are_restored() -> None:
    _, vault = mask_request({"messages": [{"role": "user", "content": DNI}]})
    body = _response(
        {"role": "assistant", "content": "tu DNI es [[ES_DNI_1]]", "refusal": "[[es_dni_1]]"}
    )

    restored = restore_response(body, vault)

    message = restored["choices"][0]["message"]
    assert message["content"] == f"tu DNI es {DNI}"
    assert message["refusal"] == DNI
    assert restored["usage"] == {"total_tokens": 3}


def test_response_tool_arguments_are_restored_as_valid_json() -> None:
    # A value with a quote and a backslash: restoring the raw JSON text would break it.
    _, vault = mask_request(
        {"messages": [{"role": "user", "content": "x"}]},
        detector=lambda text: (
            [Span(0, 1, EntityType.EMAIL, Layer.PATTERN, Confidence.HIGH)] if text == "x" else []
        ),
    )
    vault_value = 'a"b\\c'
    vault._by_token["EMAIL_1"] = vault_value  # force a hostile value for this unit test
    arguments = json.dumps({"to": "[[EMAIL_1]]", "n": 1, "k": ["[[EMAIL_1]] y"]})
    body = _response(
        {"role": "assistant", "tool_calls": [{"id": "c", "function": {"arguments": arguments}}]}
    )

    restored = restore_response(body, vault)

    sent = restored["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(sent) == {"to": vault_value, "n": 1, "k": [f"{vault_value} y"]}


def test_response_invalid_tool_arguments_are_restored_as_text() -> None:
    _, vault = mask_request({"messages": [{"role": "user", "content": DNI}]})
    body = _response({"tool_calls": [{"function": {"arguments": "[[ES_DNI_1]] {"}}]})

    restored = restore_response(body, vault)

    assert restored["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == (
        f"{DNI} {{"
    )


def test_response_unknown_fields_pass_untouched() -> None:
    _, vault = mask_request({"messages": [{"role": "user", "content": DNI}]})
    body = _response({"content": None, "otro": "[[ES_DNI_1]]"})
    body["extra"] = "[[ES_DNI_1]]"

    restored = restore_response(body, vault)

    assert restored["extra"] == "[[ES_DNI_1]]"
    assert restored["choices"][0]["message"]["otro"] == "[[ES_DNI_1]]"
    assert restored["choices"][0]["message"]["content"] is None


def test_response_with_unexpected_shape_passes_untouched() -> None:
    _, vault = mask_request({"messages": [{"role": "user", "content": DNI}]})
    body: dict[str, object] = {"choices": "raro"}

    assert restore_response(body, vault) == {"choices": "raro"}
    assert restore_response({"choices": [1, {"message": 2}]}, vault) == {
        "choices": [1, {"message": 2}]
    }
