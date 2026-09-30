"""Red team helpers: proxies for both providers against the fake upstream that records bytes."""

import contextlib
import json
import unicodedata
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from antifaz.api.app import create_app
from antifaz.config import Settings
from antifaz.detect.normalize import normalize
from antifaz.detect.scan import scan
from antifaz.mask import Detector
from tests.integration import test_proxy_anthropic as anth
from tests.integration import test_proxy_openai as oai
from tests.integration.fakes import GATEWAY_KEY, PROVIDER_KEY, FakeUpstream, compact

# Synthetic, checksum-valid values (never real people).
DNI = "12345678Z"
NIE = "X1234567L"
IBAN = "ES9121000418450200051332"
PHONE = "612345678"

OPENAI_AUTH = {"Authorization": f"Bearer {GATEWAY_KEY}"}
ANTHROPIC_AUTH = {"x-api-key": GATEWAY_KEY, "anthropic-version": "2023-06-01"}


def _strings(node: object) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)
    elif node is not None:
        yield str(node)


def sent_texts(upstream: FakeUpstream) -> list[str]:
    """Raw bodies and every decoded JSON string that reached the fake provider."""
    texts: list[str] = []
    for request in upstream.requests:
        raw = request.content.decode("utf-8", errors="replace")
        texts.append(raw)
        with contextlib.suppress(ValueError):
            texts.extend(_strings(json.loads(raw, parse_int=str, parse_float=str)))
    return texts


def assert_not_sent(upstream: FakeUpstream, *values: str) -> None:
    """No value reaches the provider in any sent text (raw body or decoded JSON string).

    Each text is checked three ways: NFKC-normalised against the value; reduced to its
    alphanumerics against the value's alphanumerics; and through the detector's own view
    (normalize(): invisible characters and combining marks dropped, look-alikes folded)
    reduced to alphanumerics, so a zero-width character between two digits hides nothing.
    """
    texts = sent_texts(upstream)
    for value in values:
        folded = compact(value)
        for text in texts:
            normal = unicodedata.normalize("NFKC", text)
            assert value not in normal
            assert folded not in compact(normal)
            assert folded not in compact(normalize(text).text)


def openai_body(content: object, **extra: Any) -> dict[str, Any]:
    return {"model": "gpt-x", "messages": [{"role": "user", "content": content}], **extra}


def anthropic_body(content: object, **extra: Any) -> dict[str, Any]:
    return {
        "model": "claude-x",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": content}],
        **extra,
    }


@pytest.fixture
def upstream_openai() -> FakeUpstream:
    return FakeUpstream(oai.echo)


@pytest.fixture
def upstream_anthropic() -> FakeUpstream:
    return FakeUpstream(anth.echo)


@pytest.fixture
def openai_proxy(upstream_openai: FakeUpstream) -> Iterator[TestClient]:
    yield from oai._client(upstream_openai)


@pytest.fixture
def anthropic_proxy(upstream_anthropic: FakeUpstream) -> Iterator[TestClient]:
    yield from anth._client(upstream_anthropic)


# --- The whole gateway: both providers at once (issue 20) ----------------------------------

GATEWAY_BODY: dict[str, Any] = {
    "model": "m",
    "max_tokens": 8,
    "messages": [{"role": "user", "content": "hola"}],
}


def both_echo(request: httpx.Request) -> httpx.Response:
    """Answers like the OpenAI or the Anthropic fake, depending on the destination."""
    if request.url.host == httpx.URL(oai.UPSTREAM).host:
        return oai.echo(request)
    return anth.echo(request)


def gateway_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "antifaz_api_key": SecretStr(GATEWAY_KEY),
        "openai_api_key": SecretStr(PROVIDER_KEY),
        "anthropic_api_key": SecretStr(PROVIDER_KEY),
        "openai_base_url": oai.UPSTREAM,
        "anthropic_base_url": anth.UPSTREAM,
        "allowed_hosts": ["testserver"],
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def gateway_client(
    upstream: FakeUpstream,
    settings: Settings | None = None,
    detector: Detector = scan,
) -> Iterator[TestClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(settings or gateway_settings(), http_client=http, detector=detector)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def upstream_any() -> FakeUpstream:
    return FakeUpstream(both_echo)


@pytest.fixture
def gateway(upstream_any: FakeUpstream) -> Iterator[TestClient]:
    yield from gateway_client(upstream_any)
