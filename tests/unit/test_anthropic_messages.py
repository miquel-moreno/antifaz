"""Field walk of the Anthropic Messages format (issue 5b, ADR-0013). Synthetic data only."""

import json

import pytest

from antifaz.errors import AttachmentBlocked, UnmaskableField
from antifaz.providers.anthropic_messages import mask_request, restore_response
from antifaz.restore import restore

DNI = "12345678Z"
OTHER = "87654321X"
SIGNATURE = "EqQBCkYIARgCKkBzaW5naXR1cmEtZmFsc2EtZGUtcHJ1ZWJh+/=="


def _thinking(text: str = "Pienso en [[ES_DNI_1]] y en [[texto]]") -> dict[str, object]:
    return {"type": "thinking", "thinking": text, "signature": SIGNATURE}


REDACTED = {"type": "redacted_thinking", "data": "RW5jcnlwdGVkLWZha2UtZGF0YQ=="}


def _user(content: object) -> dict[str, object]:
    return {
        "model": "claude-x",
        "max_tokens": 10,
        "messages": [{"role": "user", "content": content}],
    }


def test_string_content_and_string_system_are_masked() -> None:
    body = {**_user(f"DNI {DNI}"), "system": f"Cliente {OTHER}"}

    masked, vault = mask_request(body)

    # system comes first in the body order only if it is first; numbering follows the walk.
    assert masked["messages"][0]["content"] == "DNI [[ES_DNI_1]]"
    assert masked["system"] == "Cliente [[ES_DNI_2]]"
    assert masked["model"] == "claude-x"
    assert masked["max_tokens"] == 10
    assert len(vault) == 2


def test_system_text_blocks_with_cache_control_are_masked() -> None:
    system = [{"type": "text", "text": f"a {DNI}", "cache_control": {"type": "ephemeral"}}]
    masked, _ = mask_request({**_user("hola"), "system": system})

    assert masked["system"] == [
        {"type": "text", "text": "a [[ES_DNI_1]]", "cache_control": {"type": "ephemeral"}}
    ]


def test_system_blocks_other_than_text_block() -> None:
    system = [{"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}}]
    with pytest.raises(AttachmentBlocked):
        mask_request({**_user("hola"), "system": system})


def test_text_blocks_tool_use_and_tool_result_are_masked() -> None:
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": f"soy {DNI}"}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "buscar",
                        "input": {"dni": DNI, "extra": {"type": "x", "data": [OTHER]}},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": f"ok {OTHER}"},
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "is_error": False,
                        "content": [{"type": "text", "text": f"y {DNI}"}],
                    },
                ],
            },
        ]
    }

    masked, vault = mask_request(body)

    text = json.dumps(masked, ensure_ascii=False)
    assert DNI not in text
    assert OTHER not in text
    tool_use = masked["messages"][1]["content"][0]
    # Tool input is data for the tool: its own "type"/"data" keys are not attachments.
    assert tool_use["input"] == {
        "dni": "[[ES_DNI_1]]",
        "extra": {"type": "x", "data": ["[[ES_DNI_2]]"]},
    }
    results = masked["messages"][2]["content"]
    assert results[0]["content"] == "ok [[ES_DNI_2]]"
    assert results[1]["content"] == [{"type": "text", "text": "y [[ES_DNI_1]]"}]
    assert results[1]["is_error"] is False
    assert len(vault) == 2


def test_a_key_inside_tool_input_with_personal_data_blocks() -> None:
    block = {"type": "tool_use", "id": "t", "name": "f", "input": {DNI: "x"}}
    with pytest.raises(UnmaskableField):
        mask_request({"messages": [{"role": "assistant", "content": [block]}]})


@pytest.mark.parametrize(
    "block",
    [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
        {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "x"}},
        {"type": "search_result", "source": "s", "title": "t", "content": []},
        {"type": "container_upload", "file_id": "file_1"},
        {"type": "server_tool_use", "id": "s", "name": "web_search", "input": {}},
        {"type": "web_search_tool_result", "tool_use_id": "s", "content": []},
        {"type": "bloque_nuevo", "text": "hola"},
        {"type": "text", "text": "hola", "source": {"x": 1}},
        {
            "type": "text",
            "text": "hola",
            "citations": [{"type": "char_location", "cited_text": "a"}],
        },
        {"type": "tool_result", "tool_use_id": "t", "content": [{"type": "image", "source": {}}]},
        {"type": "tool_result", "tool_use_id": "t", "content": [{"type": "tool_use", "id": "x"}]},
        {"type": "text", "text": "data:image/png;base64,AAAA"},
    ],
)
def test_blocks_outside_the_allowlist_block(block: dict[str, object]) -> None:
    with pytest.raises(AttachmentBlocked):
        mask_request(_user([block]))


def test_attachment_keys_block_in_unknown_top_level_fields() -> None:
    for extra in ({"source": {"x": 1}}, {"file_id": "f"}, {"type": "document"}):
        with pytest.raises(AttachmentBlocked):
            mask_request({**_user("hola"), "nuevo": extra})


def test_config_fields_with_type_are_allowed() -> None:
    body = {
        **_user("hola"),
        "thinking": {"type": "enabled", "budget_tokens": 2048},
        "tool_choice": {"type": "auto"},
        "tools": [
            {
                "name": "buscar",
                "description": f"Busca el cliente {DNI}",
                "input_schema": {"type": "object", "properties": {"data": {"type": "string"}}},
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 3},
        ],
        "metadata": {"user_id": f"usuario {DNI}"},
    }

    masked, _ = mask_request(body)

    assert masked["tools"][0]["description"] == "Busca el cliente [[ES_DNI_1]]"
    assert masked["tools"][0]["input_schema"] == body["tools"][0]["input_schema"]  # type: ignore[index]
    assert masked["tools"][1] == body["tools"][1]  # type: ignore[index]
    assert masked["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert masked["metadata"] == {"user_id": "usuario [[ES_DNI_1]]"}


def test_numbers_with_personal_data_block() -> None:
    with pytest.raises(UnmaskableField):
        mask_request({**_user("hola"), "nuevo": 612345678})


def test_thinking_blocks_are_copied_untouched() -> None:
    thinking = _thinking()
    body = {
        "messages": [
            {"role": "user", "content": f"DNI {DNI}"},
            {
                "role": "assistant",
                "content": [thinking, REDACTED, {"type": "text", "text": "[[ES_DNI_1]]"}],
            },
        ]
    }

    masked, _ = mask_request(body)

    content = masked["messages"][1]["content"]
    assert content[0] == thinking  # "[[" not escaped, signature identical
    assert content[1] == REDACTED  # "data" key allowed only here
    assert content[2] == {"type": "text", "text": "[[!ES_DNI_1]]"}  # user text is escaped
    assert masked["messages"][0]["content"] == "DNI [[ES_DNI_1]]"


def test_thinking_with_personal_data_blocks() -> None:
    # Thinking cannot be masked (the signature would break), so a value in it blocks.
    body = {"messages": [{"role": "assistant", "content": [_thinking(f"el DNI {DNI}")]}]}
    with pytest.raises(UnmaskableField):
        mask_request(body)


def test_thinking_shape_inside_tool_input_is_masked_not_skipped() -> None:
    fake = {"type": "thinking", "thinking": DNI, "signature": DNI}
    block = {"type": "tool_use", "id": "t", "name": "f", "input": fake}

    masked, _ = mask_request({"messages": [{"role": "assistant", "content": [block]}]})

    assert DNI not in json.dumps(masked)


def test_input_is_not_modified() -> None:
    body = _user([{"type": "text", "text": DNI}, _thinking()])
    before = json.dumps(body)

    mask_request(body)

    assert json.dumps(body) == before


def test_same_value_same_placeholder_across_system_and_messages() -> None:
    body = {
        "system": [{"type": "text", "text": DNI}],
        "messages": [{"role": "user", "content": f"{OTHER} {DNI}"}],
    }

    masked, _ = mask_request(body)

    assert masked["system"][0]["text"] == "[[ES_DNI_1]]"
    assert masked["messages"][0]["content"] == "[[ES_DNI_2]] [[ES_DNI_1]]"


# --- Response ---------------------------------------------------------------------------


def test_response_text_and_tool_use_input_are_restored() -> None:
    _, vault = mask_request(_user(f"DNI {DNI}"))
    thinking = _thinking("uso [[ES_DNI_1]]")
    body = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            thinking,
            REDACTED,
            {"type": "text", "text": "Tu DNI es [[ES_DNI_1]]", "citations": None},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "f",
                "input": {"dni": "[[ES_DNI_1]]", "lista": ["[[ES_DNI_1]] x", 3, True]},
            },
            {"type": "bloque_nuevo", "text": "[[ES_DNI_1]]"},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 3, "output_tokens": 4},
    }

    restored = restore_response(body, vault)

    content = restored["content"]
    assert content[0] == thinking  # never restored (invariant 9)
    assert content[1] == REDACTED
    assert content[2]["text"] == f"Tu DNI es {DNI}"
    assert content[3]["input"] == {"dni": DNI, "lista": [f"{DNI} x", 3, True]}
    assert content[4] == {"type": "bloque_nuevo", "text": "[[ES_DNI_1]]"}  # unknown: untouched
    assert restored["usage"] == body["usage"]
    assert body["content"][2]["text"] == "Tu DNI es [[ES_DNI_1]]"  # type: ignore[index]


def test_response_with_unexpected_shape_passes_untouched() -> None:
    _, vault = mask_request(_user(DNI))

    assert restore_response({"content": "raro"}, vault) == {"content": "raro"}
    weird = {"content": [1, {"type": "text", "text": 2}, {"type": "tool_use", "input": "s"}]}
    assert restore_response(weird, vault) == weird


def test_restored_text_equals_restore() -> None:
    _, vault = mask_request(_user(f"{DNI} {OTHER}"))
    text = "[[ES_DNI_2]] [[ES_DNI_1]] [[!x]]"

    restored = restore_response({"content": [{"type": "text", "text": text}]}, vault)

    assert restored["content"][0]["text"] == restore(text, vault)
