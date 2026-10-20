"""Red team of «En directo» (issue 53, PR 11e-1, ADR-0018, invariants 12, 13 and 15).

- Invariant 15: the sentinel DNI goes through the proxy (OpenAI, Anthropic, count_tokens, a
  streamed answer, a guard block), «Prueba un texto» and /antifaz/scan while a stream is open,
  and once more before a second stream connects: the SSE bytes never hold the sentinel, its
  marker (ES_DNI_1) or anything shaped like a marker ([[...]]).
- Owner condition 3, memory only: over that whole run, with every logger at DEBUG, the logs hold
  neither the sentinel nor any event's content (types, origins, outcomes, clients), and nothing
  opens or writes a file.
- The door: a cookie of the other scheme, the API key alone, the admin token as a bearer, and a
  page of another site or port (Sec-Fetch-Site other than same-origin) never open the stream.
- Too many streams: a flood from one session holds at most 2 open; the rest end `busy` at once
  and leave no subscriber behind.
"""

import builtins
import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import pytest

from antifaz.live import Client, Origin, Outcome, Provider, Reason
from tests.conftest import SENTINEL_DNI
from tests.integration.fakes import GATEWAY_KEY, misses_repeats
from tests.integration.live_client import (
    Stream,
    action,
    build_app,
    csrf_of,
    log_in,
    running,
)
from tests.integration.panel_client import ADMIN_TOKEN, LOCAL_URL

AUTH = {"Authorization": f"Bearer {GATEWAY_KEY}"}
ANTHROPIC = {"x-api-key": GATEWAY_KEY, "anthropic-version": "2023-06-01"}
MARKER = re.compile(rb"\[\[|ES_DNI_\d")
# What an event is made of: none of it may reach a log line.
EVENT_WORDS = (
    "ES_DNI",
    *(member.value for member in Origin),
    *(member.value for member in Outcome if member is not Outcome.MASKED),
    *(member.value for member in Reason if member is not Reason.NONE),
    *(member.value for member in Client if member is not Client.OTHER),
    *(member.value for member in Provider if member is not Provider.NONE),
)


def _chat(**extra: Any) -> dict[str, Any]:
    return {"model": "m", "messages": [{"role": "user", "content": f"DNI {SENTINEL_DNI}"}], **extra}


def _anthropic() -> dict[str, Any]:
    return {"model": "m", "max_tokens": 8, "messages": [{"role": "user", "content": SENTINEL_DNI}]}


class _NoFiles:
    """Every way to open a file fails the test while it is on."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.opened: list[str] = []
        self._monkeypatch = monkeypatch

    def on(self) -> None:
        def refuse(*args: object, **_: object) -> None:
            self.opened.append(type(args[0]).__name__ if args else "?")
            raise AssertionError("a file was opened during the live run")

        for owner, name in (
            (builtins, "open"),
            (io, "open"),
            (os, "open"),
            (Path, "open"),
            (Path, "write_text"),
            (Path, "write_bytes"),
        ):
            self._monkeypatch.setattr(owner, name, refuse)

    def off(self) -> None:
        self._monkeypatch.undo()


async def test_the_sentinel_never_reaches_the_stream_the_logs_or_a_file(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = build_app()
    guarded = build_app(detector=misses_repeats)  # leaves the second DNI: the guard blocks
    files = _NoFiles(monkeypatch)
    raw = b""
    with caplog.at_level(logging.DEBUG):
        async with running(app) as client, running(guarded) as guarded_client:
            session_id = await log_in(client)
            token = await csrf_of(client, session_id)
            guarded_id = await log_in(guarded_client)
            files.on()
            async with Stream(app, session_id) as stream, Stream(guarded, guarded_id) as other:
                await stream.event()
                await other.event()
                await client.post("/v1/chat/completions", json=_chat(), headers=AUTH)
                await client.post("/v1/chat/completions", json=_chat(stream=True), headers=AUTH)
                await client.post("/v1/messages", json=_anthropic(), headers=ANTHROPIC)
                count = {k: v for k, v in _anthropic().items() if k != "max_tokens"}
                await client.post("/v1/messages/count_tokens", json=count, headers=ANTHROPIC)
                await client.post("/antifaz/scan", json={"text": SENTINEL_DNI}, headers=AUTH)
                await client.post(
                    "/panel/api/try",
                    content=json.dumps({"text": f"Mi DNI {SENTINEL_DNI}", "lang": "es"}).encode(),
                    headers=action(session_id, token),
                )
                two = {
                    "model": "m",
                    "messages": [{"role": "user", "content": f"{SENTINEL_DNI} {SENTINEL_DNI}"}],
                }
                blocked = await guarded_client.post("/v1/chat/completions", json=two, headers=AUTH)
                assert blocked.status_code == 400
                names = [(await stream.event())[0] for _ in range(6)]
                assert names == ["request"] * 6
                assert (await other.event())[0] == "request"
            async with Stream(app, session_id) as again:  # the reconnect's snapshot
                name, data = await again.event()
                assert name == "snapshot" and len(data["recent"]) == 6
            files.off()
            raw = stream.raw + other.raw + again.raw

    assert files.opened == []
    assert SENTINEL_DNI.encode() not in raw
    assert not MARKER.search(raw)
    assert b"retry:" not in raw
    assert SENTINEL_DNI not in caplog.text
    for word in EVENT_WORDS:
        assert word not in caplog.text, word


# --- The door ------------------------------------------------------------------------------------


async def _refused(stream: Stream) -> int:
    async with stream:
        assert await stream.closed()
    assert b"event:" not in stream.raw
    assert stream.status is not None
    return stream.status


async def test_a_cookie_of_the_other_scheme_never_opens_the_stream() -> None:
    app = build_app()
    async with running(app) as https_client, running(app, LOCAL_URL) as local_client:
        https_id = await log_in(https_client)
        local_id = await log_in(local_client)
        # The https session's id in the http://localhost cookie, and the other way round.
        assert await _refused(Stream(app, https_id, scheme="http", host="localhost")) == 401
        assert await _refused(Stream(app, local_id)) == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": f"Bearer {GATEWAY_KEY}"},
        {"x-api-key": GATEWAY_KEY},
        {"Authorization": f"Bearer {ADMIN_TOKEN}"},
    ],
    ids=["api-key-bearer", "api-key-header", "admin-token-bearer"],
)
async def test_a_key_or_the_admin_token_alone_never_opens_the_stream(
    headers: dict[str, str],
) -> None:
    app = build_app()
    async with running(app):
        assert await _refused(Stream(app, None, headers)) == 401


@pytest.mark.parametrize("fetch_site", ["cross-site", "same-site", "none"])
async def test_a_page_of_another_site_or_port_never_opens_the_stream(fetch_site: str) -> None:
    app = build_app()
    async with running(app) as client:
        session_id = await log_in(client)
        stream = Stream(app, session_id, {"Sec-Fetch-Site": fetch_site})
        assert await _refused(stream) == 403
        assert b"csrf_rejected" in stream.raw


async def test_a_flood_of_streams_from_one_session_holds_two_at_most() -> None:
    app = build_app()
    async with running(app) as client:
        session_id = await log_in(client)
        streams = [Stream(app, session_id) for _ in range(20)]
        for stream in streams:
            await stream.__aenter__()
        first = [(await stream.event())[0] for stream in streams]
        assert first.count("snapshot") == 2
        assert first.count("end") == 18
        for stream in streams[2:]:
            assert await stream.closed()
            assert stream.raw.endswith(b'data: {"reason":"busy"}\n\n')
        assert app.state.live_streams.open_streams == 2
        assert len(app.state.live_feed._subscribers) == 2
        for stream in streams:
            await stream.__aexit__()
        assert app.state.live_streams.open_streams == 0
        assert app.state.live_feed._subscribers == []
