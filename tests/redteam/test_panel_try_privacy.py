"""Red team of «Prueba un texto» (issue 53, PR 11d-a, ADR-0018).

Invariant 15, scoped exception: the sentinel DNI comes back ONLY in the answer to the very Try
request that sent it. Never in the logs (everything at DEBUG, uvicorn's loggers included), the
counters, GET /panel/api/status, the panel pages, another session's answer, any error body
(400, 413, 429, and 500 forced by a restore that fails with the sentinel in its message) or a
blocked answer (a detector that fails with the sentinel in its message), and nothing is written
to disk while a Try runs (open for writing, os.open with a write flag and tempfile are watched).
The body is never parsed as a form.

Also: XSS payloads come back only inside JSON (application/json, nosniff, CSP default-src
'none'); a marker the user forged is never restored (invariant 6); a cross-site form or no-cors
fetch never reaches the route; and a flood of Try requests never starves a proxy request,
because Try runs behind its own one-thread limiter before the proxy's mask limiter.
"""

import ast
import builtins
import inspect
import io
import json
import logging
import os
import tempfile
import threading
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from antifaz.api.app import MASK_THREADS
from antifaz.detect.scan import scan
from antifaz.detect.types import Span
from antifaz.web import routes, try_text
from tests.conftest import SENTINEL_DNI
from tests.integration.fakes import GATEWAY_KEY
from tests.integration.panel_client import (
    HTTPS_URL,
    LOCAL_URL,
    action_headers,
    cookie_of,
    logged_in,
    panel_client,
    panel_settings,
)

TRY = "/panel/api/try"
TEXT = f"Mi DNI es {SENTINEL_DNI}, escríbeme."
LOGGERS = ("", "uvicorn", "uvicorn.access", "uvicorn.error", "antifaz", "antifaz.panel")


def _body(text: str = TEXT, lang: str = "es", **extra: object) -> bytes:
    return json.dumps({"text": text, "lang": lang, **extra}).encode()


def _try(client: TestClient, session: tuple[str, str], content: bytes, **extra: str) -> Any:
    headers = action_headers(client, session[0], session[1], **extra)
    return client.post(TRY, content=content, headers=headers)


@pytest.fixture(params=[HTTPS_URL, LOCAL_URL])
def client(request: pytest.FixtureRequest) -> Iterator[TestClient]:
    with panel_client(request.param, settings=panel_settings(log_level="DEBUG")) as client:
        yield client


def _debug_everywhere(caplog: pytest.LogCaptureFixture) -> None:
    for name in LOGGERS:
        caplog.set_level(logging.DEBUG, logger=name)


def _log_text(caplog: pytest.LogCaptureFixture) -> str:
    return caplog.text + "\n".join(repr(record.__dict__) for record in caplog.records)


# --- invariant 15: the sentinel only in its own answer ---------------------------------------


def test_the_sentinel_comes_back_only_in_its_own_answer(
    client: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _debug_everywhere(caplog)
    mine = logged_in(client)
    other = logged_in(client)

    own = _try(client, mine, _body())
    assert own.status_code == 200
    assert own.text.count(SENTINEL_DNI) == 1  # the restored value, once
    assert own.headers["cache-control"] == "no-store"

    leaks: list[str] = []
    # Error bodies, each with the sentinel in what was sent.
    leaks.append(_try(client, mine, _body(extra=SENTINEL_DNI)).text)  # 400
    leaks.append(_try(client, mine, _body(lang=SENTINEL_DNI)).text)  # 400
    leaks.append(_try(client, mine, _body(ner=SENTINEL_DNI)).text)  # 400 (11d-b: only a boolean)
    too_long = _body(f"{SENTINEL_DNI} " + "a" * try_text.MAX_TEXT_BYTES)
    leaks.append(_try(client, mine, too_long).text)  # 413
    # Another session's answer.
    leaks.append(_try(client, other, _body("hola, sin datos")).text)
    # The panel's other pages and data.
    cookie = {"Cookie": f"{cookie_of(client)}={mine[0]}", "Sec-Fetch-Site": "same-origin"}
    leaks.append(client.get("/panel/api/status", headers=cookie).text)
    for lang in ("es", "en"):
        leaks.append(client.get(f"/panel?lang={lang}", headers=cookie).text)
    # A 500 forced by a restore that fails with the sentinel in its message.
    real_restore = try_text.restore

    def broken_restore(*_: object, **__: object) -> str:
        raise RuntimeError(f"restore failed on {SENTINEL_DNI}")

    monkeypatch.setattr(try_text, "restore", broken_restore)
    failed = _try(client, mine, _body())
    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "internal_error"
    leaks.append(failed.text)
    monkeypatch.setattr(try_text, "restore", real_restore)
    # 429 with the sentinel in the body that was refused.
    while (response := _try(client, mine, _body())).status_code == 200:
        assert SENTINEL_DNI in response.text
    assert response.status_code == 429
    leaks.append(response.text)
    leaks.append(json.dumps(dict(response.headers)))

    state = client.app.state  # type: ignore[attr-defined]
    leaks.append(repr(vars(state.stream_counters)))
    leaks.append(repr(state.panel_state.try_limiter._tries))
    for text in leaks:
        assert SENTINEL_DNI not in text
        assert "[[ES_DNI_" not in text
    assert caplog.records  # the gateway logged while the sentinel went through
    assert SENTINEL_DNI not in _log_text(caplog)
    assert "restore failed" not in _log_text(caplog)  # only the error's type is logged


def test_a_failing_detector_blocks_without_the_sentinel_anywhere(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _debug_everywhere(caplog)

    def broken(_: str) -> list[Span]:
        raise RuntimeError(f"detector failed on {SENTINEL_DNI}")

    with panel_client(HTTPS_URL, detector=broken) as client:
        session = logged_in(client)

        response = _try(client, session, _body())

    assert response.status_code == 200
    assert response.json()["blocked_reason"] == "detector_failed"
    assert SENTINEL_DNI not in response.text
    assert SENTINEL_DNI not in _log_text(caplog)


def test_a_try_without_the_ner_keeps_the_sentinel_to_its_own_answer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """11d-b: `"ner": false` takes the plain scanner; the same invariant 15 holds."""
    from tests.nerfakes import CARMEN, NAMES, fake_detector

    _debug_everywhere(caplog)
    ner, predictor = fake_detector(NAMES)
    with panel_client(HTTPS_URL, ner=ner, settings=panel_settings(log_level="DEBUG")) as client:
        session = logged_in(client)

        response = _try(client, session, _body(f"{CARMEN}: {TEXT}", ner=False))
        status = client.get(
            "/panel/api/status",
            headers={
                "Cookie": f"{cookie_of(client)}={session[0]}",
                "Sec-Fetch-Site": "same-origin",
            },
        )

    assert response.status_code == 200
    assert response.json()["names_checked"] is False
    assert response.text.count(SENTINEL_DNI) == 1
    assert predictor.texts == []  # the text never went to the NER
    for text in (status.text, _log_text(caplog)):
        assert SENTINEL_DNI not in text and CARMEN not in text


def test_the_uvicorn_access_log_stays_off() -> None:
    """The access log would hold the path and query (never the body); antifaz serve keeps it
    off (tests/unit/test_cli_serve.py checks the options); this pins it for the Try route."""
    from antifaz.cli import serve

    assert serve.OPTIONS["access_log"] is False


class _DiskWatch:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_open = builtins.open
        real_os_open = os.open
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

        def watched_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
            if any(flag in mode for flag in "wax+"):
                self.writes.append(f"open {mode}")
            return real_open(file, mode, *args, **kwargs)

        def watched_os_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            if flags & write_flags:
                self.writes.append("os.open")
            return real_os_open(path, flags, *args, **kwargs)

        def refused(*_: Any, **__: Any) -> Any:
            self.writes.append("tempfile")
            raise OSError("no temporary files")

        monkeypatch.setattr(builtins, "open", watched_open)
        monkeypatch.setattr(io, "open", watched_open)
        monkeypatch.setattr(os, "open", watched_os_open)
        for name in (
            "mkstemp",
            "mkdtemp",
            "NamedTemporaryFile",
            "TemporaryFile",
            "SpooledTemporaryFile",
        ):
            monkeypatch.setattr(tempfile, name, refused)


def test_nothing_is_written_to_disk_and_no_form_is_parsed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = logged_in(client)
    assert _try(client, session, _body()).status_code == 200  # warm-up: lazy imports done
    from starlette.requests import Request

    forms: list[str] = []

    async def no_form(*_: Any, **__: Any) -> Any:
        forms.append("form")
        raise AssertionError("the body was parsed as a form")

    monkeypatch.setattr(Request, "form", no_form)
    watch = _DiskWatch()
    watch.install(monkeypatch)

    responses = [
        _try(client, session, _body()),
        _try(client, session, _body(extra=1)),
        _try(client, session, _body("a" * (try_text.MAX_TEXT_BYTES + 1))),
    ]

    assert [r.status_code for r in responses] == [200, 400, 413]
    assert watch.writes == []
    assert forms == []


def test_the_route_never_calls_form_or_writes() -> None:
    source = inspect.getsource(routes)
    tree = ast.parse(source)
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "stream" in calls  # not an empty pass: the body is read as a stream
    assert not {"form", "write", "write_text", "write_bytes"} & calls


# --- XSS: the payload only ever travels inside JSON ------------------------------------------

XSS = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "</script><script>alert(1)</script>",
    "<svg/onload=alert(1)>",
    "{{7*7}} ${7*7} <%= 7*7 %>",
    "javascript:alert(1)",
    chr(0x2028) + "alert(1)" + chr(0x2029),
]


@pytest.mark.parametrize("payload", XSS)
def test_xss_payloads_come_back_only_as_json_data(client: TestClient, payload: str) -> None:
    session = logged_in(client)
    text = f"{payload} {SENTINEL_DNI} {payload}"

    response = _try(client, session, _body(text))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.content.lstrip()[:1] == b"{"
    sent = "".join(s.get("text", s.get("marker", "")) for s in response.json()["sent"])
    assert sent == f"{payload} [[ES_DNI_1]] {payload}"  # data, untouched, never interpreted
    cookie = {"Cookie": f"{cookie_of(client)}={session[0]}"}
    page = client.get("/panel", headers=cookie).text
    assert payload not in page


# --- invariant 6: a forged marker is never restored ------------------------------------------


@pytest.mark.parametrize(
    "forged", ["[[ES_DNI_9]]", "[[ES_DNI_1]]", "[[ es_dni_1 ]]", "[[PERSON_1]]", "[[!ES_DNI_1]]"]
)
def test_a_forged_marker_is_never_restored(client: TestClient, forged: str) -> None:
    session = logged_in(client)

    data = _try(client, session, _body(f"{forged} {SENTINEL_DNI} {forged}")).json()

    markers = [s for s in data["sent"] if "marker" in s]
    assert markers == [{"marker": "[[ES_DNI_1]]", "type": "ES_DNI"}]
    assert [s["value"] for s in data["reply"] if "marker" in s] == [SENTINEL_DNI]
    sent = "".join(s.get("text", s.get("marker", "")) for s in data["sent"])
    assert forged not in sent.replace("[[ES_DNI_1]]", "", 1) or forged.startswith("[[!")
    reply = "".join(s.get("text", s.get("value", "")) for s in data["reply"])
    assert reply.count(SENTINEL_DNI) == 1
    assert "ES_DNI_9" not in reply


# --- cross-site requests never reach the route -----------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        # A form of another site posting text/plain with a JSON body (the cookie travels only
        # if SameSite were broken; it is sent here anyway, the worst case).
        {"Content-Type": "text/plain", "Origin": "https://evil.example", "Sec-Fetch-Site": ""},
        {"Content-Type": "text/plain", "Origin": "null", "Sec-Fetch-Site": "cross-site"},
        # fetch(..., {mode: "no-cors"}) from another localhost port (same-site: the cookie does
        # travel), text/plain.
        {
            "Content-Type": "text/plain;charset=UTF-8",
            "Origin": "http://localhost:9999",
            "Sec-Fetch-Site": "same-site",
        },
        # Even JSON with a leaked token, from another site.
        {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    ],
)
def test_cross_site_requests_never_reach_the_route(
    client: TestClient, headers: dict[str, str]
) -> None:
    session = logged_in(client)
    limiter = client.app.state.panel_state.try_limiter  # type: ignore[attr-defined]
    sent = {k: v for k, v in action_headers(client, *session).items()}
    sent.update(headers)

    response = client.post(TRY, content=_body(), headers={k: v for k, v in sent.items() if v})

    assert response.status_code == 403
    assert SENTINEL_DNI not in response.text
    assert len(limiter) == 0  # refused before the route: not even counted


# --- a Try flood never starves the proxy -----------------------------------------------------

SLOW = "lento"


def test_a_try_flood_never_starves_a_proxy_request() -> None:
    release = threading.Event()
    entered = threading.Event()

    def detector(text: str) -> list[Span]:
        if SLOW in text:
            entered.set()
            release.wait(timeout=30)
        return scan(text)

    tries = MASK_THREADS + 4
    with panel_client(HTTPS_URL, detector=detector) as client:
        session = logged_in(client)
        app_state = client.app.state  # type: ignore[attr-defined]
        results: list[int] = []

        def one_try() -> None:
            results.append(_try(client, session, _body(f"{SLOW} {SENTINEL_DNI}")).status_code)

        flood = [threading.Thread(target=one_try) for _ in range(tries)]
        proxy: list[httpx.Response] = []
        try:
            for thread in flood:
                thread.start()
            assert entered.wait(timeout=30)
            assert app_state.try_limiter.borrowed_tokens == 1
            assert app_state.mask_limiter.borrowed_tokens == 1  # the panel holds one thread

            def proxy_request() -> None:
                proxy.append(
                    client.post(
                        "/v1/chat/completions",
                        json={"model": "m", "messages": [{"role": "user", "content": "hola"}]},
                        headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
                    )
                )

            worker = threading.Thread(target=proxy_request)
            worker.start()
            worker.join(timeout=30)
            assert proxy and proxy[0].status_code == 200  # served while the flood waits
        finally:
            release.set()
            for thread in flood:
                thread.join(timeout=60)
    # One Try runs; the others are refused at once (503 try_busy), never queued.
    assert sorted(results) == [200] + [503] * (tries - 1)


# --- with the NER on: a Try never starves the proxy nor trips its circuit breaker ---------


def _ner_pool(sleep_seconds: float) -> Any:
    from antifaz.detect.ner.cache import SpanCache
    from antifaz.detect.ner.engine import NerDetector
    from antifaz.detect.ner.pool import NerPool
    from tests.nerfakes import CARMEN, FAKE_FACTORY

    pool = NerPool(
        FAKE_FACTORY,
        {"names": {CARMEN: "person"}, "triggers": True, "sleep_seconds": sleep_seconds},
        workers=1,
        timeout=20.0,
        failure_threshold=1,
    )
    return pool, NerDetector(pool, cache=SpanCache(100))


def _chat(client: TestClient, text: str) -> httpx.Response:
    return client.post(
        "/v1/chat/completions",
        json={"model": "m", "messages": [{"role": "user", "content": text}]},
        headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
    )


def test_the_proxy_is_served_during_a_try_flood_with_the_ner_on() -> None:
    from tests.nerfakes import CARMEN

    pool, ner = _ner_pool(sleep_seconds=0.5)  # each Try holds the only worker for 0.5 s
    with panel_client(HTTPS_URL, ner=ner) as client:
        session = logged_in(client)
        tries: list[int] = []

        def flood() -> None:
            for _ in range(6):
                text = f"FAKE_SLEEP {CARMEN} {SENTINEL_DNI}"
                tries.append(_try(client, session, _body(text)).status_code)

        flooder = threading.Thread(target=flood)
        flooder.start()
        try:
            served = [_chat(client, f"Soy {CARMEN}") for _ in range(3)]
        finally:
            flooder.join(timeout=60)

    assert [r.status_code for r in served] == [200, 200, 200]
    for response in served:
        assert response.json()["choices"][0]["message"]["content"] == f"Soy {CARMEN}"
    assert set(tries) <= {200, 503} and len(tries) == 6
    assert pool.status() == "closed"  # closed by the lifespan at the end, never circuit_open


def test_a_hanging_try_never_trips_the_proxy_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from antifaz.api import app as app_module
    from tests.nerfakes import CARMEN

    monkeypatch.setattr(app_module, "TRY_NER_SECONDS", 0.5)
    pool, ner = _ner_pool(sleep_seconds=60)  # FAKE_SLEEP hangs the worker
    with panel_client(HTTPS_URL, ner=ner) as client:
        session = logged_in(client)
        assert _chat(client, "calentar").status_code == 200  # the worker is loaded

        hung = _try(client, session, _body(f"FAKE_SLEEP {CARMEN}"))

        assert hung.status_code == 200
        assert hung.json()["blocked_reason"] == "detector_failed"
        assert pool._failures == 0  # threshold 1: one counted failure would open the circuit
        assert pool.status() != "circuit_open"
        give_up = time.monotonic() + 60  # the replaced worker loads in the background
        while (proxied := _chat(client, f"Soy {CARMEN}")).status_code != 200:
            assert time.monotonic() < give_up
            time.sleep(0.2)
        assert proxied.json()["choices"][0]["message"]["content"] == f"Soy {CARMEN}"
