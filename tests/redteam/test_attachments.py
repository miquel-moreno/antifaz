"""Attachments (images, PDF, audio, file_id) cannot be inspected: they must be blocked anywhere."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.integration.fakes import FakeUpstream
from tests.redteam.conftest import ANTHROPIC_AUTH, OPENAI_AUTH, anthropic_body, openai_body

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="  # noqa: E501
IMAGE_URL = {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG}"}}
ANT_IMAGE = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}}
ANT_PDF = {"type": "document", "source": {"type": "file", "file_id": "file_abc"}}

OPENAI_ATTACKS: dict[str, dict[str, Any]] = {
    "imagen_en_usuario": openai_body([{"type": "text", "text": "mira"}, IMAGE_URL]),
    "imagen_por_url_remota": openai_body(
        [{"type": "image_url", "image_url": {"url": "https://x.test/dni.png"}}]
    ),
    "audio": openai_body([{"type": "input_audio", "input_audio": {"data": PNG, "format": "wav"}}]),
    "fichero_file_id": openai_body([{"type": "file", "file": {"file_id": "file-abc"}}]),
    "fichero_file_data": openai_body(
        [{"type": "file", "file": {"filename": "a.pdf", "file_data": PNG}}]
    ),
    "imagen_en_system": {
        "model": "m",
        "messages": [{"role": "system", "content": [IMAGE_URL]}, {"role": "user", "content": "x"}],
    },
    "imagen_en_resultado_de_tool": {
        "model": "m",
        "messages": [{"role": "tool", "tool_call_id": "c", "content": [IMAGE_URL]}],
    },
    "tipo_inventado": openai_body([{"type": "input_video", "video": "abc"}]),
    "clave_data_en_campo_desconocido": openai_body("x", x_extra={"data": PNG}),
    "data_url_en_argumentos": {
        "model": "m",
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c",
                        "type": "function",
                        "function": {
                            "name": "f",
                            "arguments": f'{{"img": "data:image/png;base64,{PNG}"}}',
                        },
                    }
                ],
            }
        ],
    },
}


@pytest.mark.parametrize("body", OPENAI_ATTACKS.values(), ids=OPENAI_ATTACKS.keys())
def test_openai_adjuntos_se_bloquean(
    openai_proxy: TestClient, upstream_openai: FakeUpstream, body: dict[str, Any]
) -> None:
    """El atacante cuela una foto del DNI, un PDF o un audio en cualquier parte del mensaje."""
    response = openai_proxy.post("/v1/chat/completions", json=body, headers=OPENAI_AUTH)
    assert response.status_code == 400
    assert upstream_openai.requests == []


ANTHROPIC_ATTACKS: dict[str, dict[str, Any]] = {
    "imagen_en_usuario": anthropic_body([ANT_IMAGE]),
    "pdf_file_id": anthropic_body([ANT_PDF]),
    "imagen_en_system": anthropic_body("x", system=[ANT_IMAGE]),
    "imagen_en_tool_result": anthropic_body(
        [{"type": "tool_result", "tool_use_id": "t", "content": [ANT_IMAGE]}]
    ),
    "search_result": anthropic_body(
        [{"type": "search_result", "source": "s", "title": "t", "content": []}]
    ),
    "container_upload": anthropic_body([{"type": "container_upload", "file_id": "f"}]),
    "source_en_tool_use_input": {
        "model": "m",
        "max_tokens": 5,
        "messages": [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t", "name": "f", "input": ANT_IMAGE}],
            }
        ],
    },
    "citations_en_texto": anthropic_body(
        [{"type": "text", "text": "x", "citations": [{"type": "char_location"}]}]
    ),
    "tipo_inventado": anthropic_body([{"type": "video", "data": PNG}]),
    "base64_en_input_schema": anthropic_body(
        "x",
        tools=[{"name": "f", "input_schema": {"type": "base64", "data": PNG}}],
    ),
}


@pytest.mark.parametrize("path", ["/v1/messages", "/v1/messages/count_tokens"])
@pytest.mark.parametrize("body", ANTHROPIC_ATTACKS.values(), ids=ANTHROPIC_ATTACKS.keys())
def test_anthropic_adjuntos_se_bloquean(
    anthropic_proxy: TestClient, upstream_anthropic: FakeUpstream, body: dict[str, Any], path: str
) -> None:
    """El atacante cuela una imagen o documento en cualquier posición de Anthropic."""
    response = anthropic_proxy.post(path, json=body, headers=ANTHROPIC_AUTH)
    assert response.status_code == 400
    assert upstream_anthropic.requests == []


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/v1/messages",
            anthropic_body(
                [{"type": "text", "text": "x", "SOURCE": {"type": "base64", "DATA": PNG}}]
            ),
        ),
        ("/v1/messages", anthropic_body("x", SOURCE={"type": "base64", "DATA": PNG})),
        ("/v1/messages", anthropic_body("x", tools=[{"name": "t", "Source": {"x": 1}}])),
        ("/v1/chat/completions", openai_body("x", x_extra={"IMAGE": PNG})),
        ("/v1/chat/completions", openai_body([{"type": "text", "text": "x", "Data": PNG}])),
        ("/v1/chat/completions", openai_body("x", File_Id="file-abc")),
    ],
)
def test_claves_de_adjunto_en_mayusculas_se_bloquean(
    openai_proxy: TestClient,
    anthropic_proxy: TestClient,
    upstream_openai: FakeUpstream,
    upstream_anthropic: FakeUpstream,
    path: str,
    body: dict[str, Any],
) -> None:
    """El atacante escribe `SOURCE` o `Data` para que la lista de adjuntos no lo vea."""
    proxy, auth = (
        (anthropic_proxy, ANTHROPIC_AUTH) if path == "/v1/messages" else (openai_proxy, OPENAI_AUTH)
    )
    response = proxy.post(path, json=body, headers=auth)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "antifaz_blocked"
    assert upstream_openai.requests == []
    assert upstream_anthropic.requests == []
