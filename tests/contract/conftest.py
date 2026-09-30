"""Contract tests: the official SDKs talk to Antifaz, which talks to a fake provider.

    SDK client --(httpx2.ASGITransport, in process)--> Antifaz --(httpx.MockTransport)--> fixtures

Nothing leaves the process: the SDKs get an `http_client` bound to the Antifaz ASGI app, and
Antifaz gets an upstream client bound to `FixtureUpstream`, which serves the hand-written
responses in `fixtures/` and records every byte it receives. As a second barrier, sockets to
anything but the loopback are refused while these tests run.
"""

import json
import os
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anthropic
import httpx
import httpx2
import openai
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from antifaz.api.app import create_app
from antifaz.config import Settings
from tests.integration.fakes import GATEWAY_KEY, PROVIDER_KEY, sse_response

FIXTURES = Path(__file__).parent / "fixtures"
OPENAI_UPSTREAM = "https://openai.invalid/v1"
ANTHROPIC_UPSTREAM = "https://anthropic.invalid"
ANTIFAZ_URL = "http://testserver"

# Synthetic values (valid check digits, invented). The fixtures answer with their placeholders.
DNI = "12345678Z"
EMAIL = "ana.garcia@example.com"
IBAN = "ES91 2100 0418 4502 0005 1332"
USER_TEXT = f"Soy Ana. Mi DNI es {DNI}, mi correo {EMAIL} y mi IBAN {IBAN}."
PLANTED = (DNI, EMAIL, IBAN)
# What the fixtures' "Hola. Tu DNI es [[ES_DNI_1]] y tu correo es [[EMAIL_1]]." becomes.
RESTORED_GREETING = f"Hola. Tu DNI es {DNI} y tu correo es {EMAIL}."
RESTORED_TOOL_INPUT = {"dni": DNI, "email": EMAIL, "iban": IBAN}


def load_json(name: str) -> dict[str, Any]:
    fixture: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return fixture


def load_sse(name: str) -> bytes:
    """The stream as the provider sends it: the file plus the blank line that ends the last
    event (files keep a single final newline, see fixtures/README.md)."""
    return (FIXTURES / name).read_bytes() + b"\n"


class FixtureUpstream:
    """The fake provider: answers with the fixture chosen by the test and records requests."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.fixture: str | None = None

    def serve(self, name: str) -> None:
        self.fixture = name

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fixture is None:
            raise AssertionError("the test did not choose a fixture")
        if self.fixture.endswith(".sse"):
            # Cut in chunks of 37 bytes: events and placeholders arrive split, as on a network.
            return sse_response(load_sse(self.fixture), split=37)[0]
        fixture = load_json(self.fixture)
        return httpx.Response(fixture["status"], json=fixture["body"])

    def sent(self) -> str:
        """Everything the provider received: URLs, headers and bodies."""
        parts = []
        for request in self.requests:
            parts.append(str(request.url))
            parts.extend(f"{name}: {value}" for name, value in request.headers.items())
            parts.append(request.content.decode("utf-8"))
        return "\n".join(parts)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


def _compact(text: str) -> str:
    return "".join(char for char in text.casefold() if char.isalnum())


def assert_nothing_leaked(upstream: FixtureUpstream) -> None:
    """No planted value (in any spacing) and not the Antifaz key reached the provider."""
    sent = upstream.sent()
    decoded = json.dumps([json.loads(r.content) for r in upstream.requests], ensure_ascii=False)
    for value in PLANTED:
        assert value not in sent
        assert _compact(value) not in _compact(sent + decoded)
    assert GATEWAY_KEY not in sent


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only loopback sockets (the event loop uses one on Windows); no SDK settings from env."""
    for name in list(os.environ):
        if name.startswith(("OPENAI_", "ANTHROPIC_")):
            monkeypatch.delenv(name)
    real_getaddrinfo = socket.getaddrinfo

    def local_only(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in ("localhost", "127.0.0.1", "::1", None):
            raise RuntimeError("contract tests must not open network connections")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", local_only)


@pytest.fixture
def upstream() -> FixtureUpstream:
    return FixtureUpstream()


def contract_settings() -> Settings:
    return Settings(
        antifaz_api_key=SecretStr(GATEWAY_KEY),
        openai_api_key=SecretStr(PROVIDER_KEY),
        openai_base_url=OPENAI_UPSTREAM,
        anthropic_api_key=SecretStr(PROVIDER_KEY),
        anthropic_base_url=ANTHROPIC_UPSTREAM,
        allowed_hosts=["testserver"],
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
async def app(upstream: FixtureUpstream) -> AsyncIterator[FastAPI]:
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    application = create_app(contract_settings(), http_client=upstream_client)
    # httpx2.ASGITransport does not run the lifespan: run it here, as a server would.
    async with application.router.lifespan_context(application):
        yield application
    await upstream_client.aclose()


def _sdk_http_client(app: FastAPI) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=ANTIFAZ_URL)


@pytest.fixture
async def openai_client(app: FastAPI) -> AsyncIterator[openai.AsyncOpenAI]:
    client = openai.AsyncOpenAI(
        api_key=GATEWAY_KEY,
        base_url=f"{ANTIFAZ_URL}/v1",
        max_retries=0,  # a 429 or 502 must reach the test, not be retried
        http_client=_sdk_http_client(app),
    )
    async with client:
        yield client


@pytest.fixture
async def anthropic_client(app: FastAPI) -> AsyncIterator[anthropic.AsyncAnthropic]:
    client = anthropic.AsyncAnthropic(
        api_key=GATEWAY_KEY,
        base_url=ANTIFAZ_URL,
        max_retries=0,
        http_client=_sdk_http_client(app),
    )
    async with client:
        yield client
