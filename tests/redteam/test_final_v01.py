"""Red team final antes de v0.1.0: cuerpos codificados, números, campos nuevos, CLI y telemetría.

Ángulos que no cubrían las rondas anteriores. Solo datos inventados y claves falsas; el
proveedor es siempre el falso que guarda los bytes recibidos.
"""

import codecs
import gzip
import io
import json
import socket
import sys
import zlib
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from antifaz import cli
from tests.conftest import _loopback, forbid_network
from tests.integration.fakes import GATEWAY_KEY, FakeUpstream
from tests.redteam.conftest import (
    ANTHROPIC_AUTH,
    DNI,
    IBAN,
    OPENAI_AUTH,
    PHONE,
    anthropic_body,
    assert_not_sent,
    gateway_client,
    openai_body,
)

PROXY_PATHS = ("/v1/chat/completions", "/v1/messages", "/v1/messages/count_tokens")
AUTH = {"Authorization": f"Bearer {GATEWAY_KEY}", "anthropic-version": "2023-06-01"}
JSON_HEADERS = {**AUTH, "Content-Type": "application/json"}
CARD = "4111111111111111"  # número de tarjeta de prueba publicado (Luhn válido)


def _body_for(path: str, content: str) -> dict[str, Any]:
    return openai_body(content) if "chat" in path else anthropic_body(content)


def _raw_post(client: TestClient, path: str, raw: bytes, **headers: str) -> httpx.Response:
    return client.post(path, content=raw, headers={**JSON_HEADERS, **headers})


# --- Cuerpos comprimidos o en otra codificación ----------------------------------------------


def _deflate(data: bytes) -> bytes:
    return zlib.compress(data)


COMPRESSED = {"gzip": gzip.compress, "deflate": _deflate}


@pytest.mark.parametrize("path", PROXY_PATHS)
@pytest.mark.parametrize("encoding", COMPRESSED)
def test_cuerpo_comprimido_se_rechaza_sin_enviar_nada(
    gateway: TestClient, upstream_any: FakeUpstream, path: str, encoding: str
) -> None:
    """El atacante comprime el cuerpo (Content-Encoding) para que el detector no lea el DNI."""
    raw = COMPRESSED[encoding](json.dumps(_body_for(path, f"DNI {DNI}")).encode())
    response = _raw_post(gateway, path, raw, **{"Content-Encoding": encoding})

    assert response.status_code == 400
    assert upstream_any.requests == []


@pytest.mark.parametrize("path", PROXY_PATHS)
def test_content_encoding_falso_con_json_plano_no_llega_al_proveedor(
    gateway: TestClient, upstream_any: FakeUpstream, path: str
) -> None:
    """Dice gzip pero manda JSON plano: se enmascara y la cabecera no viaja al proveedor."""
    raw = json.dumps(_body_for(path, f"DNI {DNI}")).encode()
    _raw_post(gateway, path, raw, **{"Content-Encoding": "gzip"})

    assert_not_sent(upstream_any, DNI)
    for request in upstream_any.requests:
        assert "content-encoding" not in request.headers


ENCODED = {
    "utf-16": lambda text: text.encode("utf-16"),  # con BOM
    "utf-16-le": lambda text: text.encode("utf-16-le"),
    "utf-32": lambda text: text.encode("utf-32"),
    "latin-1": lambda text: text.encode("latin-1"),
    "bom-utf-8": lambda text: codecs.BOM_UTF8 + text.encode("utf-8"),
}


@pytest.mark.parametrize("path", PROXY_PATHS)
@pytest.mark.parametrize("charset", ENCODED)
def test_cuerpo_en_otra_codificacion_se_rechaza(
    gateway: TestClient, upstream_any: FakeUpstream, path: str, charset: str
) -> None:
    """El cuerpo llega en UTF-16, UTF-32, latin-1 o con BOM: nunca se interpreta a medias."""
    text = json.dumps(_body_for(path, f"Señor, DNI {DNI}"), ensure_ascii=False)
    response = _raw_post(gateway, path, ENCODED[charset](text))

    assert response.status_code == 400
    assert DNI not in response.text
    assert upstream_any.requests == []


@pytest.mark.parametrize("charset", ["utf-16", "iso-8859-1", "utf-8; foo=bar", "UTF8"])
def test_charset_distinto_de_utf8_en_la_cabecera_es_415(
    gateway: TestClient, upstream_any: FakeUpstream, charset: str
) -> None:
    """El atacante declara otro charset para que la pasarela y el proveedor lean distinto."""
    raw = json.dumps(openai_body(f"DNI {DNI}")).encode()
    content_type = f"application/json; charset={charset}"
    response = _raw_post(gateway, "/v1/chat/completions", raw, **{"Content-Type": content_type})

    assert response.status_code == 415
    assert upstream_any.requests == []


NOT_JSON = {
    "comentario": '{"model": "m", /* DNI %s */ "messages": []}',
    "coma_final": '{"model": "m", "messages": [{"role": "user", "content": "DNI %s"},]}',
    "comillas_simples": "{'model': 'm', 'messages': [{'role': 'user', 'content': 'DNI %s'}]}",
    "json5_sin_comillas": '{model: "m", messages: [{role: "user", content: "DNI %s"}]}',
    "dos_objetos": '{"model": "m", "messages": []}{"x": "DNI %s"}',
    "basura_detras": '{"model": "m", "messages": []} DNI %s',
}


@pytest.mark.parametrize("path", PROXY_PATHS)
@pytest.mark.parametrize("case", NOT_JSON)
def test_json_relajado_se_rechaza(
    gateway: TestClient, upstream_any: FakeUpstream, path: str, case: str
) -> None:
    """JSON con comentarios, comas finales o dos objetos: un lector tolerante vería otra cosa."""
    response = _raw_post(gateway, path, (NOT_JSON[case] % DNI).encode())

    assert response.status_code == 400
    assert DNI not in response.text
    assert upstream_any.requests == []


# --- Números con datos -----------------------------------------------------------------------

NUMBERS = {
    "telefono_float": f"{PHONE}.0",
    "telefono_exponente": f"{PHONE}e0",
    "telefono_cientifico": "6.12345678e8",
    "telefono_negativo": f"-{PHONE}",
    "telefono_con_prefijo": f"34{PHONE}",
    "tarjeta_entera": CARD,
    "tarjeta_float": f"{CARD}.0",
    "tarjeta_cientifica": "4.111111111111111e15",
}


@pytest.mark.parametrize("path", PROXY_PATHS)
@pytest.mark.parametrize("case", NUMBERS)
def test_dato_escrito_como_numero_json_no_llega(
    gateway: TestClient, upstream_any: FakeUpstream, path: str, case: str
) -> None:
    """Un teléfono o una tarjeta escritos como número JSON con decimales o exponente."""
    body = json.dumps(_body_for(path, "hola"))[:-1] + f', "x_num": {NUMBERS[case]}}}'
    _raw_post(gateway, path, body.encode())

    assert_not_sent(upstream_any, PHONE if "telefono" in case else CARD)


def test_entero_enorme_no_rompe_la_pasarela(
    gateway: TestClient, upstream_any: FakeUpstream
) -> None:
    """Un entero de 5000 cifras (más que el límite de Python al pasarlo a texto): 400, no 500."""
    body = json.dumps(openai_body("hola"))[:-1] + ', "x": ' + "9" * 5000 + "}"
    response = _raw_post(gateway, "/v1/chat/completions", body.encode())

    assert response.status_code == 400
    assert upstream_any.requests == []


# --- Campos de las APIs que no se habían probado ---------------------------------------------

_NESTED: dict[str, Any] = {"type": "object"}
_cursor = _NESTED
for _ in range(80):  # 80 niveles: por debajo del tope de profundidad
    _cursor["properties"] = {"p": {"type": "object"}}
    _cursor = _cursor["properties"]["p"]
_cursor["description"] = f"titular {DNI}"

OPENAI_FIELDS = {
    "stop": openai_body("hola", stop=[f"DNI {DNI}", IBAN]),
    "n_y_logprobs": openai_body(f"DNI {DNI}", n=3, logprobs=True, top_logprobs=5),
    "logit_bias_con_clave": openai_body("hola", logit_bias={PHONE: 1}),
    "response_format_json_schema": openai_body(
        "hola",
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "ficha",
                "schema": {
                    "type": "object",
                    "properties": {
                        "dni": {"type": "string", "enum": [DNI], "description": f"p. ej. {IBAN}"}
                    },
                },
            },
        },
    ),
    "prediction": openai_body(
        "hola", prediction={"type": "content", "content": f"El DNI es {DNI}"}
    ),
    "seed_telefono": openai_body("hola", seed=int(PHONE)),
    "esquema_de_tool_muy_anidado": openai_body(
        "hola",
        tools=[
            {
                "type": "function",
                "function": {"name": "f", "parameters": _NESTED},
            }
        ],
    ),
}


@pytest.mark.parametrize("case", OPENAI_FIELDS)
def test_openai_campos_poco_usados_no_dejan_pasar_datos(
    gateway: TestClient, upstream_any: FakeUpstream, case: str
) -> None:
    """stop, logit_bias, response_format, prediction, seed o un esquema de 80 niveles con datos."""
    gateway.post("/v1/chat/completions", json=OPENAI_FIELDS[case], headers=OPENAI_AUTH)

    assert_not_sent(upstream_any, DNI, IBAN, PHONE)


ANTHROPIC_FIELDS = {
    "system_con_cache_control": anthropic_body(
        "hola",
        system=[{"type": "text", "text": f"DNI {DNI}", "cache_control": {"type": "ephemeral"}}],
    ),
    "cache_control_con_dato": anthropic_body(
        [{"type": "text", "text": "hola", "cache_control": {"type": "ephemeral", "ttl": DNI}}]
    ),
    "stop_sequences": anthropic_body("hola", stop_sequences=[DNI, f"IBAN {IBAN}"]),
    "tool_choice_name": anthropic_body("hola", tool_choice={"type": "tool", "name": f"f_{DNI}"}),
    "thinking_budget_telefono": anthropic_body(
        "hola", thinking={"type": "enabled", "budget_tokens": int(PHONE)}
    ),
    "mcp_servers_url": anthropic_body(
        "hola", mcp_servers=[{"type": "url", "url": f"https://mcp.test/?dni={DNI}", "name": "x"}]
    ),
    "container": anthropic_body("hola", container=f"ctr_{IBAN}"),
}


@pytest.mark.parametrize("path", ["/v1/messages", "/v1/messages/count_tokens"])
@pytest.mark.parametrize("case", ANTHROPIC_FIELDS)
def test_anthropic_campos_poco_usados_no_dejan_pasar_datos(
    gateway: TestClient, upstream_any: FakeUpstream, path: str, case: str
) -> None:
    """cache_control, stop_sequences, tool_choice, thinking, mcp_servers o container con datos."""
    gateway.post(path, json=ANTHROPIC_FIELDS[case], headers=ANTHROPIC_AUTH)

    assert_not_sent(upstream_any, DNI, IBAN, PHONE)


@pytest.mark.parametrize(
    "block",
    [
        {"type": "image", "source": {"type": "url", "url": "https://img.test/a.png"}},
        {"type": "document", "source": {"type": "text", "data": f"DNI {DNI}"}},
        {"type": "search_result", "source": "x", "title": "t", "content": []},
    ],
)
def test_adjunto_dentro_de_tool_result_se_bloquea(
    gateway: TestClient, upstream_any: FakeUpstream, block: dict[str, Any]
) -> None:
    """Una imagen o un documento metidos dentro del resultado de una herramienta."""
    body = anthropic_body([{"type": "tool_result", "tool_use_id": "t1", "content": [block]}])
    response = gateway.post("/v1/messages", json=body, headers=ANTHROPIC_AUTH)

    assert response.status_code == 400
    assert upstream_any.requests == []


@pytest.mark.parametrize("path", ["/v1/messages/count_tokens", "/v1/messages"])
def test_marcador_inventado_en_count_tokens_no_sirve_luego_en_messages(path: str) -> None:
    """Primero count_tokens con un DNI y luego messages con [[ES_DNI_1]]: no hay tabla común."""
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("count_tokens"):
            return httpx.Response(200, json={"input_tokens": 3})
        text = "Tu DNI es [[ES_DNI_1]]"
        message = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        }
        return httpx.Response(200, json=message)

    upstream = FakeUpstream(answer)
    for client in gateway_client(upstream):
        first = client.post(
            "/v1/messages/count_tokens", json=anthropic_body(f"DNI {DNI}"), headers=ANTHROPIC_AUTH
        )
        assert first.status_code == 200
        second = client.post(path, json=anthropic_body("¿cuál era?"), headers=ANTHROPIC_AUTH)
        assert DNI not in second.text
    assert_not_sent(upstream, DNI)


# --- Respuestas: campos que no se restauran ---------------------------------------------------


def test_logprobs_y_choices_extra_no_reciben_el_dato() -> None:
    """La respuesta trae logprobs con el marcador: solo se restaura content, nunca los tokens."""

    def answer(request: httpx.Request) -> httpx.Response:
        token = {"token": "[[ES_DNI_1]]", "logprob": -0.1, "bytes": list(b"[[ES_DNI_1]]")}
        choice = {
            "index": 1,
            "message": {"role": "assistant", "content": "[[ES_DNI_1]]"},
            "logprobs": {"content": [{**token, "top_logprobs": [token]}]},
            "finish_reason": "stop",
        }
        return httpx.Response(200, json={"object": "chat.completion", "choices": [choice]})

    upstream = FakeUpstream(answer)
    for client in gateway_client(upstream):
        response = client.post(
            "/v1/chat/completions", json=openai_body(f"DNI {DNI}", n=2), headers=OPENAI_AUTH
        )
    choice = response.json()["choices"][0]
    assert choice["message"]["content"] == DNI  # el cliente sí recupera su dato
    assert DNI not in json.dumps(choice["logprobs"])  # campos desconocidos: tal cual
    assert_not_sent(upstream, DNI)


# --- README: "fail-closed" y "sin telemetría" -------------------------------------------------


def test_campo_desconocido_con_texto_se_enmascara_no_se_bloquea(
    gateway: TestClient, upstream_any: FakeUpstream
) -> None:
    """README dice que un campo de texto desconocido bloquea: en realidad se enmascara."""
    body = openai_body("hola", campo_nuevo_2027={"nota": f"DNI {DNI}"})
    response = gateway.post("/v1/chat/completions", json=body, headers=OPENAI_AUTH)

    assert response.status_code == 200
    assert_not_sent(upstream_any, DNI)


def test_la_pasarela_no_abre_ninguna_conexion_propia(
    monkeypatch: pytest.MonkeyPatch, upstream_any: FakeUpstream
) -> None:
    """Telemetría: usar todas las rutas no abre ni un socket fuera del proveedor falso."""
    forbid_network(monkeypatch)
    opened: list[object] = []
    real_connect = socket.socket.connect

    def spy(sock: socket.socket, address: Any) -> None:
        # El bucle de eventos de Windows usa un par de sockets en loopback: no es una salida.
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address[0]):
            opened.append(address)
        real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", spy)
    for client in gateway_client(upstream_any):
        client.get("/healthz")
        client.get("/v1/models", headers=OPENAI_AUTH)
        client.post("/antifaz/scan", json={"text": f"DNI {DNI}"}, headers=OPENAI_AUTH)
        client.post("/v1/chat/completions", json=openai_body("hola"), headers=OPENAI_AUTH)
        client.post("/v1/messages", json=anthropic_body("hola"), headers=ANTHROPIC_AUTH)
    assert opened == []
    analytics = {"sentry_sdk", "posthog", "analytics", "segment", "mixpanel", "opentelemetry"}
    assert not analytics & {name.split(".")[0] for name in sys.modules}


# --- CLI ---------------------------------------------------------------------------------------


class _Stdin:
    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)


class _Stdout:
    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, text: str) -> int:
        self.buffer.write(text.encode())
        return len(text)

    def flush(self) -> None:
        pass


def _run_cli(monkeypatch: pytest.MonkeyPatch, command: str, data: bytes) -> tuple[int, str, str]:
    out, err = _Stdout(), io.StringIO()
    monkeypatch.setattr(sys, "stdin", _Stdin(data))
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    code = cli.main([command, "-"])
    return code, out.buffer.getvalue().decode("utf-8", "replace"), err.getvalue()


@pytest.mark.parametrize("command", ["mask", "scan"])
def test_cli_stdin_enorme_no_muestra_el_dato(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    """8 MB por stdin con el DNI al final: la salida nunca lleva el valor."""
    data = ("relleno " * 1_000_000 + f"DNI {DNI} IBAN {IBAN}\n").encode()
    code, out, err = _run_cli(monkeypatch, command, data)

    assert DNI not in out and IBAN not in out
    assert DNI not in err and IBAN not in err
    assert code in (0, 2)


@pytest.mark.parametrize(
    "data",
    [
        codecs.BOM_UTF8 + f"DNI {DNI}".encode(),
        f"DNI {DNI}".encode("utf-16"),
        f"Señor DNI {DNI}".encode("latin-1"),
        f"DNI {DNI}".encode() + b"\xff\xfe",
    ],
)
def test_cli_stdin_con_bom_u_otra_codificacion(
    monkeypatch: pytest.MonkeyPatch, data: bytes
) -> None:
    """Stdin con BOM, UTF-16, latin-1 o bytes rotos: se enmascara o da error sin el valor."""
    code, out, err = _run_cli(monkeypatch, "mask", data)

    assert DNI not in out and DNI not in err
    if code == 0:
        assert "[[ES_DNI_1]]" in out
    else:
        assert err == "antifaz: cannot read the input as UTF-8 text\n"
