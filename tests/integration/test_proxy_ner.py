"""The proxy with the NER on (ADR-0016): real worker processes running the fake backend.

Names never reach the provider (invariant 2, guard off), a hung or dead NER worker blocks the
request with 400 and the next request works (invariant 7), and neither the gateway nor the
workers write a name or a DNI anywhere (invariant 8). All names are invented.
"""

import asyncio
import json
import logging
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from antifaz import guard
from antifaz.api.app import create_app
from antifaz.detect.ner.cache import SpanCache
from antifaz.detect.ner.engine import NerDetector
from antifaz.detect.ner.pool import NerPool
from antifaz.detect.scan import Scanner
from tests.conftest import SENTINEL_DNI
from tests.integration import test_proxy_anthropic as anthropic_tests
from tests.integration import test_proxy_openai as openai_tests
from tests.integration.fakes import GATEWAY_KEY, FakeUpstream, compact
from tests.nerfakes import CARMEN, FAKE_FACTORY, JORDI

OPENAI_AUTH = {"Authorization": f"Bearer {GATEWAY_KEY}"}
ANTHROPIC_AUTH = {"x-api-key": GATEWAY_KEY, "anthropic-version": "2023-06-01"}
NAMES = [CARMEN, JORDI]


def _ner() -> NerDetector:
    pool = NerPool(
        FAKE_FACTORY,
        {"names": {name: "person" for name in NAMES}, "triggers": True, "sleep_seconds": 60},
        workers=1,
        timeout=2.0,
    )
    return NerDetector(pool, cache=SpanCache(100))


def _clients(
    upstream: FakeUpstream, provider: str = "openai", **settings: object
) -> Iterator[TestClient]:
    tests = openai_tests if provider == "openai" else anthropic_tests
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(tests._settings(**settings), http_client=http, ner=_ner())
    with TestClient(app) as client:
        yield client


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream(openai_tests.echo)


def _chat(text: str, *others: str) -> dict[str, object]:
    messages = [{"role": "user", "content": other} for other in others]
    return {"model": "m", "messages": [*messages, {"role": "user", "content": text}]}


def _sent(upstream: FakeUpstream) -> str:
    (request,) = upstream.requests
    return json.dumps(json.loads(request.content), ensure_ascii=False)


def test_names_are_masked_and_restored(upstream: FakeUpstream) -> None:
    for client in _clients(upstream):
        response = client.post(
            "/v1/chat/completions", json=_chat(f"Soy {CARMEN}"), headers=OPENAI_AUTH
        )
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == f"Soy {CARMEN}"
    assert "[[PERSON_1]]" in _sent(upstream)


def test_invariant_2_names_never_reach_the_provider_with_the_guard_off(
    upstream: FakeUpstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guard, "check", lambda payload, vault: None)  # ONLY in this test
    body = {
        "model": "m",
        "messages": [
            {"role": "system", "content": f"Cliente: {CARMEN}, DNI {SENTINEL_DNI}"},
            {"role": "user", "content": [{"type": "text", "text": f"Hola, soy {JORDI}"}]},
            # other spellings the fake NER does not know: propagation masks them
            {"role": "user", "content": "carmen prueba lópez y JORDI INVENTAT PUIG"},
        ],
        "user": CARMEN,
    }
    for client in _clients(upstream):
        assert (
            client.post("/v1/chat/completions", json=body, headers=OPENAI_AUTH).status_code == 200
        )
    sent = _sent(upstream)
    for value in [*NAMES, SENTINEL_DNI]:
        assert value not in sent
        assert compact(value) not in compact(sent)


def test_the_anthropic_route_masks_names_too() -> None:
    upstream = FakeUpstream(anthropic_tests.echo)
    body = {
        "model": "m",
        "max_tokens": 10,
        "system": f"Atiende a {JORDI}",
        "messages": [{"role": "user", "content": f"Soy {CARMEN}"}],
    }
    for client in _clients(upstream, "anthropic"):
        response = client.post("/v1/messages", json=body, headers=ANTHROPIC_AUTH)
        assert response.status_code == 200
        assert response.json()["content"][0]["text"] == f"Soy {CARMEN}"
    sent = _sent(upstream)
    assert all(compact(name) not in compact(sent) for name in NAMES)


@pytest.mark.parametrize("trigger", ["FAKE_SLEEP", "FAKE_CRASH", "FAKE_RAISE", "FAKE_MALFORMED"])
def test_a_failing_ner_blocks_the_request_and_the_next_one_works(
    upstream: FakeUpstream, trigger: str
) -> None:
    for client in _clients(upstream):
        blocked = client.post(
            "/v1/chat/completions", json=_chat(f"{trigger} {CARMEN}"), headers=OPENAI_AUTH
        )
        assert blocked.status_code == 400
        assert blocked.json()["error"]["code"] == "antifaz_blocked"
        assert CARMEN not in blocked.text
        assert upstream.requests == []  # nothing was sent
        after = client.post("/v1/chat/completions", json=_chat(f"Soy {JORDI}"), headers=OPENAI_AUTH)
        assert after.status_code == 200
        assert JORDI not in _sent(upstream)


def test_invariant_8_no_name_or_dni_in_logs_or_output(
    upstream: FakeUpstream,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    secret = f"{CARMEN} {SENTINEL_DNI}"
    # The workers start here, so they inherit the captured stdout and stderr.
    for client in _clients(upstream, log_level="DEBUG"):
        for text in (
            secret,
            f"FAKE_PRINT {secret}",
            f"FAKE_RAISE {secret}",
            f"FAKE_SLEEP {secret}",
        ):
            client.post("/v1/chat/completions", json=_chat(text), headers=OPENAI_AUTH)
    captured = capfd.readouterr()
    for value in (CARMEN, SENTINEL_DNI):
        assert value not in caplog.text
        assert value not in captured.out
        assert value not in captured.err


def test_detection_runs_outside_the_event_loop(upstream: FakeUpstream) -> None:
    loops: list[bool] = []
    scanner = Scanner()

    def detector(text: str) -> list[object]:
        try:
            asyncio.get_running_loop()
            loops.append(True)
        except RuntimeError:
            loops.append(False)
        return scanner(text)  # type: ignore[return-value]

    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(openai_tests._settings(), http_client=http, detector=detector)  # type: ignore[arg-type]
    with TestClient(app) as client:
        client.post("/v1/chat/completions", json=_chat("hola"), headers=OPENAI_AUTH)
    assert loops and not any(loops)
