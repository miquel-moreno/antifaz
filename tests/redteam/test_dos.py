"""Denial of service: huge bodies, long histories, regex backtracking and deep JSON."""

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
from tests.timing import Work, assert_linear, cpu_budget

MAX_BODY = 4 * 1024 * 1024
# Loose limits in CPU seconds (tests/timing.py, cpu_budget) for the inputs of a fixed size.
# CPU time ignores the time spent waiting for a busy CPU, but not everything: with every core
# busy the clock drops and cores are shared, and the same work used about twice the CPU time.
# Measured on a desktop on 2026-10-18 under coverage, idle / with 15 CPU burners: body 3.2 /
# 6.2 s, history 1.6 / 3.1 s, 5 000 DNIs 0.22 / 0.45 s. The limits keep at least 2.4 times the
# worst of those. With the scanner 10 times slower the 4 MiB body takes ~30 s of CPU: it would
# pass the old 30 s of wall time and fails 15 s. See docs/TECNICO.md.
BODY_CPU_SECONDS = 15
HISTORY_CPU_SECONDS = 10
MANY_DNIS_CPU_SECONDS = 2

# Inputs designed to trigger catastrophic backtracking in the usual PII regexes: a head, a unit
# repeated n times, a tail and the size of the old fixed-time test. The speed is measured by its
# shape (tests/timing.py): scanning 4n repetitions must cost well under 8 times n, never the 16
# times of a quadratic regex. 4n is at least the old size, so a pattern that is quadratic only
# from there on is still caught.
BACKTRACKING = {
    "digitos": ("", "1", "", 200_000),
    "digitos_con_espacios": ("", "1 ", "", 100_000),
    "digitos_con_puntos": ("", "12.", "", 70_000),
    "email_sin_arroba": ("", "a.", "@", 100_000),
    "email_arrobas": ("", "a@", "", 100_000),
    "email_dominio": ("x@", "a-", "", 100_000),
    "iban_casi": ("ES", "1 ", "", 100_000),
    "letras_mayusculas": ("", "Calle ", "de", 40_000),
    "nie_casi": ("X", "1", "", 200_000),
    "telefono_prefijos": ("", "+34 ", "", 50_000),
    "ipv4_casi": ("", "1.", "", 100_000),
    "fecha_casi": ("", "01/", "", 70_000),
}


@pytest.mark.parametrize(
    ("head", "unit", "tail", "size"), BACKTRACKING.values(), ids=BACKTRACKING.keys()
)
def test_retroceso_de_regex_acaba_rapido(head: str, unit: str, tail: str, size: int) -> None:
    """El atacante manda un texto pensado para que las expresiones regulares se cuelguen."""

    def prepare(n: int) -> Work:
        text = head + unit * n + tail
        return lambda: scan(text)

    assert_linear(prepare, start=size // 4)


def test_texto_enorme_cerca_del_limite(
    openai_proxy: TestClient, upstream_openai: FakeUpstream
) -> None:
    """El atacante manda casi 4 MiB de texto con un DNI escondido en medio."""
    filler = "palabra " * ((MAX_BODY - 1024) // 16)
    text = f"{filler} DNI {DNI} {filler}"
    with cpu_budget(BODY_CPU_SECONDS, "a 4 MiB body through the proxy"):
        response = openai_proxy.post(
            "/v1/chat/completions", json=openai_body(text), headers=OPENAI_AUTH
        )
    assert response.status_code in (200, 400)
    assert_not_sent(upstream_openai, DNI)


def test_el_coste_del_proxy_crece_con_el_texto(
    openai_proxy: TestClient, upstream_openai: FakeUpstream
) -> None:
    """Un cuerpo 4 veces mayor cuesta unas 4 veces más, nunca 16: mandar texto no bloquea.

    process_time: the TestClient runs the app in another thread of this process."""
    statuses: list[int] = []

    def prepare(n: int) -> Work:
        filler = "palabra " * (n // 2)
        body = openai_body(f"{filler} DNI {DNI} {filler}")

        def work() -> None:
            response = openai_proxy.post("/v1/chat/completions", json=body, headers=OPENAI_AUTH)
            statuses.append(response.status_code)

        return work

    words = (MAX_BODY - 4096) // len("palabra ")  # 4n: the size of test_texto_enorme
    assert_linear(prepare, start=words // 4, limit=words)
    assert set(statuses) <= {200, 400}
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


def _history(turns: int) -> dict[str, object]:
    messages = []
    for i in range(turns):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"turno {i} DNI {DNI} IBAN {IBAN}"})
    messages.append({"role": "user", "content": "fin"})
    return {"model": "m", "max_tokens": 5, "messages": messages}


def test_historial_muy_largo(anthropic_proxy: TestClient, upstream_anthropic: FakeUpstream) -> None:
    """El atacante manda un historial de miles de turnos con datos repartidos."""
    with cpu_budget(HISTORY_CPU_SECONDS, "a history of 3 000 turns through the proxy"):
        response = anthropic_proxy.post(
            "/v1/messages", json=_history(3_000), headers=ANTHROPIC_AUTH
        )
    assert response.status_code in (200, 400)
    assert_not_sent(upstream_anthropic, DNI, IBAN)


def test_el_coste_del_historial_crece_con_los_turnos(
    anthropic_proxy: TestClient, upstream_anthropic: FakeUpstream
) -> None:
    """4 veces más turnos cuestan unas 4 veces más, nunca 16 (process_time: the app runs in
    another thread of this process)."""
    statuses: list[int] = []

    def prepare(n: int) -> Work:
        body = _history(n)

        def work() -> None:
            response = anthropic_proxy.post("/v1/messages", json=body, headers=ANTHROPIC_AUTH)
            statuses.append(response.status_code)

        return work

    assert_linear(prepare, start=3_000 // 4, limit=20_000)  # 20 000 turns fit in 4 MiB
    assert set(statuses) <= {200, 400}
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
    """El atacante manda miles de DNI distintos para inflar la tabla de sustituciones: 4 veces
    más DNI cuestan unas 4 veces más, nunca 16, y ninguno sale en claro."""
    letters = "TRWAGMYFPDXBNJZSQVHLCKE"

    def dnis(n: int) -> list[str]:
        return [f"{k:08d}{letters[k % 23]}" for k in range(10_000_000, 10_000_000 + n)]

    def prepare(n: int) -> Work:
        text = " ".join(dnis(n))
        return lambda: mask(text)

    assert_linear(prepare, start=5_000 // 4)
    old_size = dnis(5_000)
    text = " ".join(old_size)
    with cpu_budget(MANY_DNIS_CPU_SECONDS, "masking 5 000 distinct DNIs"):
        result = mask(text)
    assert not any(d in result.text for d in old_size)


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
