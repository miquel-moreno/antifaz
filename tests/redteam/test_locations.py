"""Personal data hidden in every corner of the request body must not reach the provider."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.integration.fakes import FakeUpstream
from tests.redteam.conftest import (
    ANTHROPIC_AUTH,
    DNI,
    IBAN,
    OPENAI_AUTH,
    PHONE,
    anthropic_body,
    assert_not_sent,
    openai_body,
)

NESTED = json.dumps({"cliente": {"cuentas": [{"iban": IBAN}], "dni": DNI}})


def _oai(proxy: TestClient, body: dict[str, Any]) -> int:
    return int(proxy.post("/v1/chat/completions", json=body, headers=OPENAI_AUTH).status_code)


def _ant(proxy: TestClient, body: dict[str, Any], path: str = "/v1/messages") -> int:
    return int(proxy.post(path, json=body, headers=ANTHROPIC_AUTH).status_code)


def _tool_call(arguments: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "f", "arguments": arguments}}
        ],
    }


# --- OpenAI ------------------------------------------------------------------------------

OPENAI_BODIES: dict[str, dict[str, Any]] = {
    "system_prompt": {
        "model": "m",
        "messages": [
            {"role": "system", "content": f"El cliente tiene DNI {DNI}"},
            {"role": "user", "content": "hola"},
        ],
    },
    "historial_antiguo": {
        "model": "m",
        "messages": [
            {"role": "user", "content": f"Mi IBAN es {IBAN}"},
            {"role": "assistant", "content": f"Anotado {IBAN}"},
            *[{"role": "user", "content": "sigue"} for _ in range(50)],
        ],
    },
    "resultado_de_herramienta": {
        "model": "m",
        "messages": [
            {"role": "user", "content": "busca"},
            _tool_call("{}"),
            {"role": "tool", "tool_call_id": "c1", "content": f"titular DNI {DNI}"},
        ],
    },
    "argumentos_de_herramienta_anidados": {
        "model": "m",
        "messages": [
            _tool_call(json.dumps({"payload": NESTED, "tel": PHONE})),
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ],
    },
    "argumentos_que_no_son_json": {
        "model": "m",
        "messages": [
            _tool_call(f"dni={DNI}; iban={IBAN}"),
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ],
    },
    "json_anidado_en_texto": openai_body(NESTED),
    "partes_de_texto": openai_body([{"type": "text", "text": f"DNI {DNI}"}]),
    "campo_name": {
        "model": "m",
        "messages": [{"role": "user", "name": f"u_{DNI}", "content": "x"}],
    },
    "campo_desconocido": openai_body("hola", x_extra={"a": [{"b": f"DNI {DNI}"}]}),
    "numero_telefono_como_numero": openai_body("hola", x_num=int(PHONE)),
    "clave_de_objeto": openai_body("hola", x_extra={f"dni {DNI}": 1}),
    "url_con_query": openai_body(f"mira https://crm.test/cliente?iban={IBAN}&dni={DNI}"),
    "descripcion_de_tool": openai_body(
        "hola",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "f",
                    "description": f"usa el DNI {DNI}",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    ),
    "usuario_y_metadata": openai_body("hola", user=f"cliente-{DNI}", metadata={"k": IBAN}),
}


@pytest.mark.parametrize("body", OPENAI_BODIES.values(), ids=OPENAI_BODIES.keys())
def test_openai_datos_en_todos_los_sitios(
    openai_proxy: TestClient, upstream_openai: FakeUpstream, body: dict[str, Any]
) -> None:
    """El atacante esconde DNI, IBAN o teléfono en un rincón del JSON de OpenAI."""
    assert _oai(openai_proxy, body) in (200, 400)
    assert_not_sent(upstream_openai, DNI, IBAN, PHONE)


# --- Anthropic ---------------------------------------------------------------------------

TOOL_USE = {"type": "tool_use", "id": "t1", "name": "f", "input": {"q": {"deep": [NESTED]}}}

ANTHROPIC_BODIES: dict[str, dict[str, Any]] = {
    "system_texto": anthropic_body("hola", system=f"Cliente con DNI {DNI}"),
    "system_bloques": anthropic_body("hola", system=[{"type": "text", "text": f"IBAN {IBAN}"}]),
    "historial_antiguo": {
        "model": "m",
        "max_tokens": 5,
        "messages": [
            {"role": "user", "content": f"Mi tel es {PHONE}"},
            {"role": "assistant", "content": [{"type": "text", "text": f"ok {PHONE}"}]},
            {"role": "user", "content": "sigue"},
        ],
    },
    "tool_use_input_anidado": {
        "model": "m",
        "max_tokens": 5,
        "messages": [
            {"role": "user", "content": "busca"},
            {"role": "assistant", "content": [TOOL_USE]},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
            },
        ],
    },
    "tool_result_texto": anthropic_body(
        [{"type": "tool_result", "tool_use_id": "t1", "content": f"DNI {DNI}"}]
    ),
    "tool_result_bloques": anthropic_body(
        [
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": [{"type": "text", "text": f"IBAN {IBAN}"}],
            }
        ]
    ),
    "metadata_user_id": anthropic_body("hola", metadata={"user_id": DNI}),
    "campo_desconocido": anthropic_body("hola", x_extra=[[f"tel {PHONE}"]]),
    "numero_suelto": anthropic_body("hola", x_num=int(PHONE)),
    "tools_descripcion_y_esquema": anthropic_body(
        "hola",
        tools=[
            {
                "name": "f",
                "description": f"DNI {DNI}",
                "input_schema": {"type": "object", "properties": {"x": {"default": IBAN}}},
            }
        ],
    ),
    "nombre_de_fichero": anthropic_body(f"sube /home/u/nominas/{DNI}_{IBAN}.pdf"),
}


@pytest.mark.parametrize("body", ANTHROPIC_BODIES.values(), ids=ANTHROPIC_BODIES.keys())
def test_anthropic_datos_en_todos_los_sitios(
    anthropic_proxy: TestClient, upstream_anthropic: FakeUpstream, body: dict[str, Any]
) -> None:
    """El atacante esconde DNI, IBAN o teléfono en un rincón del JSON de Anthropic."""
    assert _ant(anthropic_proxy, body) in (200, 400)
    assert_not_sent(upstream_anthropic, DNI, IBAN, PHONE)


@pytest.mark.parametrize("body", ANTHROPIC_BODIES.values(), ids=ANTHROPIC_BODIES.keys())
def test_count_tokens_datos_en_todos_los_sitios(
    anthropic_proxy: TestClient, upstream_anthropic: FakeUpstream, body: dict[str, Any]
) -> None:
    """El atacante usa count_tokens (que parece inofensivo) para sacar los datos."""
    assert _ant(anthropic_proxy, body, "/v1/messages/count_tokens") in (200, 400)
    assert_not_sent(upstream_anthropic, DNI, IBAN, PHONE)


def test_thinking_editado_con_un_dato_se_bloquea(
    anthropic_proxy: TestClient, upstream_anthropic: FakeUpstream
) -> None:
    """El atacante edita un bloque de razonamiento firmado y le mete un DNI."""
    body = {
        "model": "m",
        "max_tokens": 5,
        "messages": [
            {"role": "user", "content": "hola"},
            {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": f"DNI {DNI}", "signature": "s"}],
            },
            {"role": "user", "content": "sigue"},
        ],
    }
    assert _ant(anthropic_proxy, body) == 400
    assert upstream_anthropic.requests == []
