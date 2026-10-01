"""Red team final antes de v0.1.0: streams con trucos nuevos y HTTP crudo contra uvicorn.

Streams: la clave del proveedor partida entre eventos que un SDK junta pero la pasarela no
(índice escrito como 0.0 o "0", miles de campos para olvidar el trozo anterior), eventos con
nombres Unicode y razonamiento, herramientas y texto mezclados. HTTP: cabeceras de
contrabando (Transfer-Encoding + Content-Length) contra el servidor real en loopback.
Solo datos inventados y claves falsas.
"""

import json
import logging
import re
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn

from antifaz.api.app import create_app
from tests.integration.fakes import GATEWAY_KEY, FakeUpstream, anthropic_event, sse_response
from tests.redteam.conftest import (
    DNI,
    IBAN,
    anthropic_body,
    assert_not_sent,
    both_echo,
    gateway_settings,
    openai_body,
)
from tests.redteam.test_streaming import CANARY, CANARY_AUTH, ROUTES, _canary_client

HEAD, TAIL = CANARY[:12], CANARY[12:]


def _openai(*choices: dict[str, Any]) -> str:
    chunks = [{"object": "chat.completion.chunk", "choices": [c]} for c in choices]
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _anthropic(*payloads: dict[str, Any]) -> str:
    return "".join(anthropic_event(p) for p in payloads) + anthropic_event({"type": "message_stop"})


def _text_delta(index: object, text: str) -> dict[str, Any]:
    return {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "text_delta", "text": text},
    }


def _client_text(route: str, sse: str) -> str:
    """El texto que un SDK reconstruiría: junta por índice convertido a entero (pydantic laxo)."""
    parts: list[str] = []
    for block in sse.split("\n\n"):
        data = [line[6:] for line in block.split("\n") if line.startswith("data: ")]
        if not data or data[0] == "[DONE]":
            continue
        try:
            payload = json.loads("\n".join(data))
        except ValueError:
            continue
        if route == "openai":
            for choice in payload.get("choices", []):
                if int(float(choice.get("index", 0))) == 0:  # sin índice: el primero
                    parts.append(choice.get("delta", {}).get("content") or "")
        elif (
            payload.get("type") == "content_block_delta"
            and int(float(payload.get("index", 0))) == 0
        ):
            parts.append(payload.get("delta", {}).get("text") or "")
    return "".join(parts)


INDEX_TRICKS = {
    "openai-float": (
        "openai",
        _openai(
            {"index": 0, "delta": {"content": HEAD}},
            {"index": 0.0, "delta": {"content": TAIL}},
            {"index": 0, "delta": {}, "finish_reason": "stop"},
        ),
    ),
    "openai-string": (
        "openai",
        _openai(
            {"index": 0, "delta": {"content": HEAD}},
            {"index": "0", "delta": {"content": TAIL}},
            {"index": 0, "delta": {}, "finish_reason": "stop"},
        ),
    ),
    "openai-sin-indice": (
        "openai",
        _openai(
            {"index": 0, "delta": {"content": HEAD}},
            {"delta": {"content": TAIL}},
            {"index": 0, "delta": {}, "finish_reason": "stop"},
        ),
    ),
    "anthropic-float": (
        "anthropic",
        _anthropic(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            _text_delta(0, HEAD),
            _text_delta(0.0, TAIL),
            {"type": "content_block_stop", "index": 0},
        ),
    ),
    "anthropic-string": (
        "anthropic",
        _anthropic(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            _text_delta(0, HEAD),
            _text_delta("0", TAIL),
            {"type": "content_block_stop", "index": 0},
        ),
    ),
}


@pytest.mark.parametrize("case", INDEX_TRICKS)
def test_clave_partida_con_indice_escrito_raro(case: str, caplog: pytest.LogCaptureFixture) -> None:
    """El proveedor parte su clave en dos deltas del mismo bloque; el segundo, con índice raro."""
    caplog.set_level(logging.DEBUG)
    route, stream = INDEX_TRICKS[case]
    upstream = FakeUpstream(lambda _: sse_response(stream)[0])
    for client in _canary_client(upstream):
        path, body = ROUTES[route][1], ROUTES[route][3]
        response = client.post(path, json=body("hola", stream=True), headers=CANARY_AUTH)
        assert CANARY not in _client_text(route, response.text), case
        assert CANARY not in caplog.text


def _eviction(route: str) -> str:
    filler = {f"x{i}": "a" for i in range(5000)}  # más campos que las colas que se recuerdan
    if route == "openai":
        return _openai(
            {"index": 0, "delta": {"content": HEAD}},
            {"index": 1, "delta": {"content": "x"}, "relleno": filler},
            {"index": 0, "delta": {"content": TAIL}},
            {"index": 0, "delta": {}, "finish_reason": "stop"},
        )
    start = {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    }
    return _anthropic(
        start,
        _text_delta(0, HEAD),
        {"type": "ping", "relleno": filler},
        _text_delta(0, TAIL),
        {"type": "content_block_stop", "index": 0},
    )


@pytest.mark.parametrize("route", ROUTES)
def test_clave_partida_con_miles_de_campos_en_medio(
    route: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Entre los dos trozos de la clave, un evento con 5000 campos para que se olvide el primero."""
    caplog.set_level(logging.DEBUG)
    stream = _eviction(route)
    upstream = FakeUpstream(lambda _: sse_response(stream)[0])
    for client in _canary_client(upstream):
        path, body = ROUTES[route][1], ROUTES[route][3]
        response = client.post(path, json=body("hola", stream=True), headers=CANARY_AUTH)
        assert CANARY not in _client_text(route, response.text)


@pytest.mark.parametrize(
    "name", ["m\u0435ssage", "message\u200b", "MESSAGE", "content_block_delta\u0301", "ping"]
)
def test_evento_con_nombre_unicode_no_restaura_ni_suelta_la_clave(name: str) -> None:
    """Evento con nombre parecido a uno conocido (cirílico, invisible) con marcador y clave."""
    payload = {"choices": [{"index": 0, "delta": {"content": "[[ES_DNI_1]]"}}]}
    stream = (
        f"event: {name}\ndata: {json.dumps(payload)}\n\n"
        f"event: {name}\ndata: {json.dumps({'k': CANARY})}\n\n"
        "data: [DONE]\n\n"
    )
    upstream = FakeUpstream(lambda _: sse_response(stream)[0])
    for client in _canary_client(upstream):
        response = client.post(
            "/v1/chat/completions", json=openai_body(f"DNI {DNI}", stream=True), headers=CANARY_AUTH
        )
        assert CANARY not in response.text
        assert DNI not in response.text  # un evento desconocido nunca se restaura
    assert_not_sent(upstream, DNI)


def test_razonamiento_herramienta_y_texto_mezclados() -> None:
    """Stream con thinking, tool_use y texto intercalados: thinking intacto, el resto restaurado."""

    def answer(request: httpx.Request) -> httpx.Response:
        thinking = {"type": "thinking", "thinking": ""}
        tool = {"type": "tool_use", "id": "t", "name": "f", "input": {}}
        text = {"type": "text", "text": ""}
        events = [
            {"type": "content_block_start", "index": 0, "content_block": thinking},
            {"type": "content_block_start", "index": 1, "content_block": tool},
            {"type": "content_block_start", "index": 2, "content_block": text},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "thinking_delta", "thinking": "uso [[ES_DNI_"}},
            {"type": "content_block_delta", "index": 1,
             "delta": {"type": "input_json_delta", "partial_json": '{"dni": "[[ES_DN'}},
            _text_delta(2, "Hola [[ES_"),
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "thinking_delta", "thinking": "1]]"}},
            {"type": "content_block_delta", "index": 1,
             "delta": {"type": "input_json_delta", "partial_json": 'I_1]]"}'}},
            _text_delta(2, "DNI_1]]"),
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "signature_delta", "signature": "sig"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_stop", "index": 1},
            {"type": "content_block_stop", "index": 2},
        ]  # fmt: skip
        return sse_response(_anthropic(*events))[0]

    upstream = FakeUpstream(answer)
    for client in _canary_client(upstream):
        response = client.post(
            "/v1/messages", json=anthropic_body(f"DNI {DNI}", stream=True), headers=CANARY_AUTH
        )
    payloads = [
        json.loads(line[6:]) for line in response.text.split("\n") if line.startswith("data: ")
    ]
    deltas = [p["delta"] for p in payloads if p.get("type") == "content_block_delta"]
    thinking = "".join(d.get("thinking", "") for d in deltas)
    tool_input = "".join(d.get("partial_json", "") for d in deltas)
    text = "".join(d.get("text", "") for d in deltas)
    assert thinking == "uso [[ES_DNI_1]]"  # invariante 9: byte a byte
    assert json.loads(tool_input) == {"dni": DNI}
    assert text == f"Hola {DNI}"
    assert_not_sent(upstream, DNI)


# --- HTTP crudo contra uvicorn (el servidor de la imagen Docker) ---------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def live() -> Iterator[tuple[int, FakeUpstream]]:
    """Uvicorn en loopback con la app real y el proveedor falso (como en el contenedor)."""
    upstream = FakeUpstream(both_echo)
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    settings = gateway_settings(allowed_hosts=["127.0.0.1"])
    app = create_app(settings, http_client=http)
    port = _free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="critical", proxy_headers=False,
        server_header=False, access_log=False,
    )  # fmt: skip
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.02)
    yield port, upstream
    server.should_exit = True
    thread.join(timeout=10)


def _exchange(port: int, raw: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=1.5) as sock:
        sock.sendall(raw)
        chunks = []  # hasta que el servidor cierre o se quede callado 1,5 s
        try:
            while chunk := sock.recv(65536):
                chunks.append(chunk)
        except (TimeoutError, ConnectionResetError):
            pass
    return b"".join(chunks)


def _statuses(reply: bytes) -> list[int]:
    return [int(code) for code in re.findall(rb"HTTP/1\.[01] (\d{3}) ", reply)]


BODY = json.dumps(openai_body("hola")).encode()
SMUGGLED_BODY = json.dumps(openai_body(f"DNI {DNI} IBAN {IBAN}")).encode()
SMUGGLED = (
    b"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1\r\n"
    b"Authorization: Bearer " + GATEWAY_KEY.encode() + b"\r\n"
    b"Content-Type: application/json\r\nContent-Length: " + str(len(SMUGGLED_BODY)).encode()
    + b"\r\n\r\n" + SMUGGLED_BODY
)  # fmt: skip


def _head(*extra: bytes) -> bytes:
    lines = [
        b"POST /v1/chat/completions HTTP/1.1",
        b"Host: 127.0.0.1",
        b"Authorization: Bearer " + GATEWAY_KEY.encode(),
        b"Content-Type: application/json",
        *extra,
    ]
    return b"\r\n".join(lines) + b"\r\n\r\n"


def _chunked(data: bytes) -> bytes:
    return f"{len(data):x}\r\n".encode() + data + b"\r\n0\r\n\r\n"


SMUGGLING = {
    # CL.TE: Content-Length solo cubre el primer cuerpo; el resto sería otra petición.
    "cl_y_te": _head(b"Content-Length: " + str(len(_chunked(BODY))).encode(),
                     b"Transfer-Encoding: chunked") + _chunked(BODY) + SMUGGLED,
    "te_ofuscado": _head(b"Content-Length: 4", b"Transfer-Encoding: xchunked") + b"{}\r\n",
    "te_doble": _head(b"Transfer-Encoding: chunked", b"Transfer-Encoding: identity")
    + _chunked(BODY),
    "cl_doble": _head(b"Content-Length: " + str(len(BODY)).encode(), b"Content-Length: 5")
    + BODY,
    "cl_negativo": _head(b"Content-Length: -1") + BODY,
    "te_con_espacio": _head(b"Transfer-Encoding : chunked") + _chunked(BODY),
    "chunk_con_extension": _head(b"Transfer-Encoding: chunked")
    + f"{len(SMUGGLED_BODY):x};dni={DNI}\r\n".encode() + SMUGGLED_BODY + b"\r\n0\r\n\r\n",
    "trailer_con_dato": _head(b"Transfer-Encoding: chunked")
    + f"{len(BODY):x}\r\n".encode() + BODY + f"\r\n0\r\nX-Dni: {DNI}\r\n\r\n".encode(),
}  # fmt: skip


@pytest.mark.parametrize("case", SMUGGLING)
def test_contrabando_http_no_lleva_datos_al_proveedor(
    live: tuple[int, FakeUpstream], case: str
) -> None:
    """Cabeceras ambiguas (TE + CL, TE doble, extensiones de trozo): nada sin enmascarar llega."""
    port, upstream = live
    reply = _exchange(port, SMUGGLING[case])

    assert_not_sent(upstream, DNI, IBAN)  # la respuesta sí puede traerlo: es del propio cliente
    assert _statuses(reply)
    assert 500 not in _statuses(reply)


def test_peticion_en_cadena_sin_clave_da_401(live: tuple[int, FakeUpstream]) -> None:
    """Dos peticiones en la misma conexión (pipelining): la segunda, sin clave, no se cuela."""
    port, upstream = live
    second = (
        b"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\nContent-Length: " + str(len(SMUGGLED_BODY)).encode()
        + b"\r\n\r\n" + SMUGGLED_BODY
    )  # fmt: skip
    reply = _exchange(port, _head(b"Content-Length: " + str(len(BODY)).encode()) + BODY + second)

    assert _statuses(reply)[-1] == 401
    assert len(upstream.requests) == 1
    assert_not_sent(upstream, DNI, IBAN)


@pytest.mark.parametrize("host", [b"127.0.0.1.evil.test", b"evil.test", b""])
def test_host_ajeno_en_http_crudo_se_rechaza(live: tuple[int, FakeUpstream], host: bytes) -> None:
    """DNS rebinding por socket: un Host que no es el nuestro no llega a ninguna ruta."""
    port, upstream = live
    raw = (
        _head(b"Content-Length: " + str(len(SMUGGLED_BODY)).encode()).replace(
            b"Host: 127.0.0.1", b"Host: " + host
        )
        + SMUGGLED_BODY
    )
    reply = _exchange(port, raw)

    assert _statuses(reply) and _statuses(reply)[0] == 400
    assert upstream.requests == []
