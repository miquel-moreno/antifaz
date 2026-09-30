"""Prompt injection: the answer brings placeholders that were not emitted in this request."""

# Look-alike characters are the attack itself.
# ruff: noqa: RUF001

import json

import httpx
import pytest

from antifaz import mask, restore
from tests.integration import test_proxy_anthropic as anth
from tests.integration import test_proxy_openai as oai
from tests.integration.fakes import FakeUpstream
from tests.redteam.conftest import ANTHROPIC_AUTH, DNI, IBAN, NIE, OPENAI_AUTH, openai_body

FOREIGN = [f"[[ES_DNI_{n}]]" for n in range(1, 10)] + [
    "[[es_dni_1]]",
    "[[ ES_DNI_1 ]]",
    "[[Es_Dni_1]]",
    "[[IBAN_1]]",
    "[[ES_NIE_1]]",
    "［［ES_DNI_1］］",  # full-width brackets
    "⟦ES_DNI_1⟧",  # mathematical white square brackets
    "[[ES_DNІ_1]]",  # Cyrillic I
    "[[ЕS_DNI_1]]",  # Cyrillic E
]
ATTACK = " ".join(FOREIGN)


def _openai_answer(text: str) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": text}}]}
    )


def test_libreria_no_restaura_con_la_tabla_de_otra_peticion() -> None:
    """La IA devuelve [[ES_DNI_1]] pero esta petición no tenía DNI: no se rellena con otro."""
    other = mask(f"DNI {DNI} IBAN {IBAN} NIE {NIE}")
    mine = mask("hola, sin datos")
    restored = restore(ATTACK, mine.vault)
    assert restored == ATTACK
    assert DNI not in restore(ATTACK, mask("nada").vault)
    assert other.text  # the other request exists but its vault is never shared


def test_libreria_marcadores_de_mas_se_quedan_tal_cual() -> None:
    """La petición tenía 1 DNI y la IA pide del 2 al 9: se quedan como marcadores."""
    result = mask(f"DNI {DNI}")
    restored = restore(" ".join(FOREIGN[1:9]), result.vault)
    assert DNI not in restored


@pytest.mark.parametrize("path", ["openai", "anthropic"])
def test_proxy_no_filtra_datos_de_una_peticion_anterior(path: str) -> None:
    """Una petición mete un DNI; la siguiente, sin datos, recibe [[ES_DNI_1]] de la IA."""
    if path == "openai":
        upstream = FakeUpstream(lambda _: _openai_answer(ATTACK))
        clients = oai._client(upstream)
        url, headers = "/v1/chat/completions", OPENAI_AUTH
        first, second = openai_body(f"DNI {DNI}"), openai_body("hola")
    else:
        upstream = FakeUpstream(
            lambda _: httpx.Response(
                200,
                json={"type": "message", "content": [{"type": "text", "text": ATTACK}]},
            )
        )
        clients = anth._client(upstream)
        url, headers = "/v1/messages", ANTHROPIC_AUTH
        first = {"model": "m", "max_tokens": 5, "messages": [{"role": "user", "content": DNI}]}
        second = {"model": "m", "max_tokens": 5, "messages": [{"role": "user", "content": "x"}]}
    for client in clients:
        client.post(url, json=first, headers=headers)
        answer = client.post(url, json=second, headers=headers)
        assert answer.status_code == 200
        assert DNI not in answer.text


def test_proxy_parecidos_unicode_no_restauran_el_dato() -> None:
    """La IA escribe el marcador con corchetes o letras de otros alfabetos para forzar un cambio."""
    lookalikes = " ".join(FOREIGN[-4:])
    upstream = FakeUpstream(lambda _: _openai_answer(lookalikes))
    for client in oai._client(upstream):
        answer = client.post(
            "/v1/chat/completions", json=openai_body(f"DNI {DNI}"), headers=OPENAI_AUTH
        )
        assert answer.status_code == 200
        assert DNI not in answer.text


def test_marcador_del_razonamiento_anterior_no_recibe_el_dato_nuevo() -> None:
    """El thinking del turno anterior tiene [[ES_DNI_1]]; el DNI nuevo no debe ocupar ese hueco."""
    body = {
        "model": "m",
        "max_tokens": 5,
        "messages": [
            {"role": "user", "content": "hola"},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Veo [[ES_DNI_1]]", "signature": "sig"},
                    {"type": "text", "text": "vale"},
                ],
            },
            {"role": "user", "content": f"Mi DNI nuevo es {DNI}"},
        ],
    }
    upstream = FakeUpstream(
        lambda _: httpx.Response(
            200,
            json={"type": "message", "content": [{"type": "text", "text": "[[ES_DNI_1]]"}]},
        )
    )
    for client in anth._client(upstream):
        answer = client.post("/v1/messages", json=body, headers=ANTHROPIC_AUTH)
        assert answer.status_code == 200
        assert DNI not in answer.text
        sent = json.loads(upstream.requests[0].content)
        assert sent["messages"][1]["content"][0]["thinking"] == "Veo [[ES_DNI_1]]"


def test_bloque_thinking_de_la_respuesta_no_se_restaura() -> None:
    """La IA devuelve un bloque de razonamiento con el marcador: debe salir intacto, sin el dato."""
    upstream = FakeUpstream(
        lambda _: httpx.Response(
            200,
            json={
                "type": "message",
                "content": [
                    {"type": "thinking", "thinking": "[[ES_DNI_1]]", "signature": "s"},
                    {"type": "text", "text": "ok"},
                ],
            },
        )
    )
    body = {"model": "m", "max_tokens": 5, "messages": [{"role": "user", "content": DNI}]}
    for client in anth._client(upstream):
        answer = client.post("/v1/messages", json=body, headers=ANTHROPIC_AUTH)
        assert answer.json()["content"][0]["thinking"] == "[[ES_DNI_1]]"
