"""Failures (detector, provider) and streaming: blocked, generic, and no value in body or logs."""

import logging
from collections.abc import Sequence
from typing import Any

import httpx
import pytest

from antifaz import Span
from tests.integration import test_proxy_anthropic as anth
from tests.integration import test_proxy_openai as oai
from tests.integration.fakes import FakeUpstream, raiser
from tests.redteam.conftest import (
    ANTHROPIC_AUTH,
    DNI,
    IBAN,
    OPENAI_AUTH,
    anthropic_body,
    openai_body,
)

ROUTES = {
    "openai": (oai, "/v1/chat/completions", OPENAI_AUTH, openai_body),
    "anthropic": (anth, "/v1/messages", ANTHROPIC_AUTH, anthropic_body),
    "count_tokens": (anth, "/v1/messages/count_tokens", ANTHROPIC_AUTH, anthropic_body),
}


def _broken_detector(text: str) -> Sequence[Span]:
    raise RuntimeError(f"detector roto con {text}")


def _empty_detector(text: str) -> Sequence[Span]:
    raise TimeoutError("ner timeout")


def _echo_error(status: int) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=request.content)

    return handler


@pytest.mark.parametrize("route", ROUTES.keys())
@pytest.mark.parametrize("detector", [_broken_detector, _empty_detector])
def test_detector_que_falla_bloquea(
    route: str, detector: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """El detector (o el NER) revienta: la petición no sale y el error no repite el DNI."""
    module, path, auth, body = ROUTES[route]
    upstream = FakeUpstream(module.echo)
    caplog.set_level(logging.DEBUG)
    for client in module._client(upstream, detector=detector):
        response = client.post(path, json=body(f"DNI {DNI}"), headers=auth)
        assert response.status_code == 400
        assert DNI not in response.text
    assert upstream.requests == []
    assert DNI not in caplog.text


@pytest.mark.parametrize("route", ROUTES.keys())
@pytest.mark.parametrize(
    "error",
    [httpx.ReadTimeout, httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError],
)
def test_fallo_del_proveedor_es_generico(
    route: str, error: type[httpx.HTTPError], caplog: pytest.LogCaptureFixture
) -> None:
    """El proveedor se cuelga o corta con un error que lleva un DNI: la respuesta no lo repite."""
    module, path, auth, body = ROUTES[route]
    caplog.set_level(logging.DEBUG)
    for client in module._client(FakeUpstream(raiser(error))):
        response = client.post(path, json=body(f"IBAN {IBAN}"), headers=auth)
        assert response.status_code in (502, 504)
        assert "12345678Z" not in response.text  # the sentinel in the httpx error message
        assert IBAN not in response.text
    assert "12345678Z" not in caplog.text
    assert IBAN not in caplog.text


@pytest.mark.parametrize("route", ROUTES.keys())
@pytest.mark.parametrize("status", [400, 429, 500, 503])
def test_error_del_proveedor_que_repite_la_peticion(
    route: str, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    """El proveedor responde un error copiando lo que recibió: solo puede llevar marcadores."""
    module, path, auth, body = ROUTES[route]
    caplog.set_level(logging.DEBUG)
    for client in module._client(FakeUpstream(_echo_error(status))):
        response = client.post(path, json=body(f"DNI {DNI} IBAN {IBAN}"), headers=auth)
        assert response.status_code == status
        assert DNI not in response.text
        assert IBAN not in response.text
    assert DNI not in caplog.text


@pytest.mark.parametrize("route", ROUTES.keys())
@pytest.mark.parametrize("stream", [True, "true", 1])
def test_streaming_se_rechaza(route: str, stream: object) -> None:
    """El atacante pide stream (o lo disfraza de texto o número) para saltarse la restauración."""
    module, path, auth, body = ROUTES[route]
    upstream = FakeUpstream(module.echo)
    for client in module._client(upstream):
        response = client.post(path, json=body(f"DNI {DNI}", stream=stream), headers=auth)
        assert response.status_code == 400
        assert DNI not in response.text
    assert upstream.requests == []


@pytest.mark.parametrize("route", ROUTES.keys())
def test_redireccion_del_proveedor_no_se_sigue(route: str) -> None:
    """El proveedor responde 307 a otra web para que reenviemos la petición allí."""
    module, path, auth, body = ROUTES[route]
    upstream = FakeUpstream(
        lambda _: httpx.Response(307, headers={"location": "https://evil.invalid/x"})
    )
    for client in module._client(upstream):
        response = client.post(path, json=body("hola"), headers=auth)
        assert response.status_code == 502
    assert len(upstream.requests) == 1
