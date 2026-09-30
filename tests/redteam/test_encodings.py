"""Encoded identifiers: what is caught and what is a documented limitation."""

import base64
import codecs
import json
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from tests.integration.fakes import FakeUpstream
from tests.redteam.conftest import DNI, IBAN, OPENAI_AUTH, assert_not_sent, openai_body

KNOWN = "known limitation (TECNICO.md): the detector and the guard do not decode {}"


def _send(proxy: TestClient, text: str) -> None:
    response = proxy.post("/v1/chat/completions", json=openai_body(text), headers=OPENAI_AUTH)
    assert response.status_code in (200, 400)


def test_escapes_json_unicode_se_detectan(
    openai_proxy: TestClient, upstream_openai: FakeUpstream
) -> None:
    """El atacante escribe el DNI con escapes JSON (barra-u-0031...) para que no se vea."""
    escaped = "".join(f"\\u{ord(c):04x}" for c in DNI)
    raw = '{"model": "m", "messages": [{"role": "user", "content": "DNI ' + escaped + '"}]}'
    response = openai_proxy.post(
        "/v1/chat/completions",
        content=raw.encode(),
        headers={**OPENAI_AUTH, "Content-Type": "application/json"},
    )
    assert response.status_code in (200, 400)
    assert_not_sent(upstream_openai, DNI)


def test_url_data_base64_se_bloquea(
    openai_proxy: TestClient, upstream_openai: FakeUpstream
) -> None:
    """El atacante mete el DNI en una URL data:...;base64, dentro del texto."""
    payload = base64.b64encode(f"DNI {DNI}".encode()).decode()
    _send(openai_proxy, f"mira data:text/plain;base64,{payload}")
    assert upstream_openai.requests == []


ENCODED = {
    "base64": base64.b64encode(f"DNI {DNI}".encode()).decode(),
    "url_encoding_completo": "".join(f"%{b:02X}" for b in DNI.encode()),
    "url_encoding_parcial": quote(DNI[:-1]) + "%5A",
    "entidades_html": "".join(f"&#{ord(c)};" for c in DNI),
    "hexadecimal": DNI.encode().hex(),
    "al_reves": DNI[::-1],
    "rot13_iban": codecs.encode(IBAN, "rot13"),
}


@pytest.mark.xfail(strict=True, reason=KNOWN.format("base64/URL/HTML/hex/reversed/rot13"))
@pytest.mark.parametrize("encoded", ENCODED.values(), ids=ENCODED.keys())
def test_dato_codificado_limite_conocido(
    openai_proxy: TestClient, upstream_openai: FakeUpstream, encoded: str
) -> None:
    """El atacante codifica el dato (base64, %XX, &#NN;, hex, al revés) y el detector no lo ve."""
    _send(openai_proxy, f"el valor es {encoded}")
    assert_not_sent(upstream_openai, encoded)


@pytest.mark.xfail(strict=True, reason="known limitation (TECNICO.md): value split across strings")
def test_dato_partido_entre_dos_cadenas_json(
    openai_proxy: TestClient, upstream_openai: FakeUpstream
) -> None:
    """El atacante parte el DNI en dos partes de texto distintas del mismo mensaje."""
    body = openai_body([{"type": "text", "text": DNI[:4]}, {"type": "text", "text": DNI[4:]}])
    openai_proxy.post("/v1/chat/completions", json=body, headers=OPENAI_AUTH)
    joined = "".join(
        json.loads(r.content)["messages"][0]["content"][i]["text"]
        for r in upstream_openai.requests
        for i in (0, 1)
    )
    assert DNI not in joined
