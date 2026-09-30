"""Denial of service: huge bodies, long histories, regex backtracking and deep JSON."""

import time
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from antifaz import mask, scan
from antifaz.api.app import create_app
from tests.integration import test_proxy_anthropic as anth
from tests.integration import test_proxy_openai as oai
from tests.integration.fakes import FakeUpstream
from tests.redteam.conftest import (
    ANTHROPIC_AUTH,
    DNI,
    IBAN,
    OPENAI_AUTH,
    anthropic_body,
    assert_not_sent,
    gateway_client,
    gateway_settings,
    openai_body,
)

MAX_BODY = 4 * 1024 * 1024

# Inputs designed to trigger catastrophic backtracking in the usual PII regexes.
BACKTRACKING = {
    "digitos": "1" * 200_000,
    "digitos_con_espacios": "1 " * 100_000,
    "digitos_con_puntos": "12." * 70_000,
    "email_sin_arroba": "a." * 100_000 + "@",
    "email_arrobas": "a@" * 100_000,
    "email_dominio": "x@" + "a-" * 100_000,
    "iban_casi": "ES" + "1 " * 100_000,
    "letras_mayusculas": "Calle " * 40_000 + "de",
    "nie_casi": "X" + "1" * 200_000,
    "telefono_prefijos": "+34 " * 50_000,
    "ipv4_casi": "1." * 100_000,
    "fecha_casi": "01/" * 70_000,
}


@pytest.mark.parametrize("text", BACKTRACKING.values(), ids=BACKTRACKING.keys())
def test_retroceso_de_regex_acaba_rapido(text: str) -> None:
    """El atacante manda un texto pensado para que las expresiones regulares se cuelguen."""
    start = time.perf_counter()
    scan(text)
    assert time.perf_counter() - start < 5


def test_texto_enorme_cerca_del_limite(
    openai_proxy: TestClient, upstream_openai: FakeUpstream
) -> None:
    """El atacante manda casi 4 MiB de texto con un DNI escondido en medio."""
    filler = "palabra " * ((MAX_BODY - 1024) // 16)
    text = f"{filler} DNI {DNI} {filler}"
    start = time.perf_counter()
    response = openai_proxy.post(
        "/v1/chat/completions", json=openai_body(text), headers=OPENAI_AUTH
    )
    assert time.perf_counter() - start < 30
    assert response.status_code in (200, 400)
    assert_not_sent(upstream_openai, DNI)


def test_cuerpo_por_encima_del_limite_es_413(
    openai_proxy: TestClient, upstream_openai: FakeUpstream
) -> None:
    """El atacante supera el tamaño máximo para agotar la memoria."""
    text = "x" * (MAX_BODY + 10)
    response = openai_proxy.post(
        "/v1/chat/completions", json=openai_body(text), headers=OPENAI_AUTH
    )
    assert response.status_code == 413
    assert upstream_openai.requests == []


def test_historial_muy_largo(anthropic_proxy: TestClient, upstream_anthropic: FakeUpstream) -> None:
    """El atacante manda un historial de miles de turnos con datos repartidos."""
    messages = []
    for i in range(3_000):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"turno {i} DNI {DNI} IBAN {IBAN}"})
    messages.append({"role": "user", "content": "fin"})
    body = {"model": "m", "max_tokens": 5, "messages": messages}
    start = time.perf_counter()
    response = anthropic_proxy.post("/v1/messages", json=body, headers=ANTHROPIC_AUTH)
    assert time.perf_counter() - start < 30
    assert response.status_code in (200, 400)
    assert_not_sent(upstream_anthropic, DNI, IBAN)


@pytest.mark.parametrize("route", ["openai", "anthropic"])
@pytest.mark.parametrize("depth", [900, 1_000, 5_000, 100_000])
def test_json_muy_profundo(route: str, depth: int) -> None:
    """El atacante anida listas cientos de veces para reventar el recorrido (y esconder un DNI)."""
    module, path, auth = (
        (oai, "/v1/chat/completions", OPENAI_AUTH)
        if route == "openai"
        else (anth, "/v1/messages", ANTHROPIC_AUTH)
    )
    raw = (
        '{"model": "m", "max_tokens": 5, "messages": [{"role": "user", "content": "x"}], "x": '
        + "[" * depth
        + f'"{DNI}"'
        + "]" * depth
        + "}"
    )
    upstream = FakeUpstream(module.echo)
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(module._settings(), http_client=http)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            path, content=raw.encode(), headers={**auth, "Content-Type": "application/json"}
        )
    assert response.status_code in (200, 400)  # a 500 means an unhandled RecursionError
    assert DNI not in response.text
    assert_not_sent(upstream, DNI)


def test_muchos_valores_distintos() -> None:
    """El atacante manda miles de DNI distintos para inflar la tabla de sustituciones."""
    letters = "TRWAGMYFPDXBNJZSQVHLCKE"
    dnis = [f"{n:08d}{letters[n % 23]}" for n in range(10_000_000, 10_005_000)]
    start = time.perf_counter()
    result = mask(" ".join(dnis))
    assert time.perf_counter() - start < 20
    assert all(d not in result.text for d in dnis[:100])


def test_muchos_campos_desconocidos(
    anthropic_proxy: TestClient, upstream_anthropic: FakeUpstream
) -> None:
    """El atacante mete un diccionario gigante de campos inventados con datos dentro."""
    extra = {f"k{i}": f"v {DNI}" for i in range(20_000)}
    response = anthropic_proxy.post(
        "/v1/messages", json=anthropic_body("x", x_extra=extra), headers=ANTHROPIC_AUTH
    )
    assert response.status_code in (200, 400)
    assert_not_sent(upstream_anthropic, DNI)


@pytest.mark.parametrize("depth", [1_000, 100_000])
def test_respuesta_del_proveedor_muy_profunda(depth: int) -> None:
    """El proveedor responde con un JSON anidado miles de veces: 502 fijo, nunca 500."""
    raw = '{"choices": ' + "[" * depth + "1" + "]" * depth + "}"
    upstream = FakeUpstream(lambda _: httpx.Response(200, content=raw.encode()))
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(oai._settings(), http_client=http)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/chat/completions", json=openai_body("hola"), headers=OPENAI_AUTH
        )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "bad_upstream_response"


def test_cuerpo_sin_content_length_por_encima_del_limite_es_413(
    upstream_any: FakeUpstream,
) -> None:
    """El atacante manda el cuerpo por trozos (sin Content-Length) para saltarse el tope."""
    settings = gateway_settings(max_body_bytes=1024)

    def chunks() -> Iterator[bytes]:
        yield b'{"model": "m", "messages": [], "x": "'
        for _ in range(10):
            yield b"a" * 512
        yield b'"}'

    for client in gateway_client(upstream_any, settings):
        response = client.post(
            "/v1/chat/completions",
            content=chunks(),
            headers={**OPENAI_AUTH, "Content-Type": "application/json"},
        )
        assert response.status_code == 413
    assert upstream_any.requests == []
