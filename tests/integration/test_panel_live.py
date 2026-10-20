"""GET /panel/api/live: «En directo» over SSE (issue 53, PR 11e-1, ADR-0018 and its 11e-1 note).

- A live session through the door, `Accept: text/event-stream`, only GET (405 otherwise, HEAD
  too). 200 text/event-stream with the door's headers, `no-store` and `X-Accel-Buffering: no`.
- Events: `snapshot` on connect, `request` per recorded request, `end` with its reason (logout,
  expired, replaced, busy, max_age, shutdown), and then the stream closes. Never a `retry:`
  field. Heartbeats are comment lines, only while a stream is open.
- Watching is NOT a use of the session: connecting or reconnecting never refreshes its last use
  (door.NOT_A_USE). The stream ends when the session would: its deadline is the earliest of 30
  minutes and the session's own time left, re-checked at that moment («Seguir» extends it).
- At most 5 streams per process and 2 per session: past that, 200 with `end: busy`.
- A subscriber's queue holds 32 events; past that the stream sends a fresh snapshot.
- With no stream open there is no task and no timer of the stream. On shutdown every stream ends.

The stream is driven in-process (tests/integration/live_client.py), never over the network.
"""

import asyncio
import json
from typing import Any

import pytest
from fastapi import FastAPI

from antifaz.detect.types import EntityType
from antifaz.live import RECENT, SUBSCRIBER_QUEUE, Client, LiveEvent, Origin, Outcome, Provider
from antifaz.live import Reason as LiveReason
from antifaz.web.headers import NO_STORE, SECURITY_HEADERS
from antifaz.web.live_stream import MAX_PER_SESSION, MAX_STREAM_SECONDS, MAX_STREAMS
from antifaz.web.sessions import IDLE_SECONDS, fingerprint
from tests.conftest import SENTINEL_DNI
from tests.integration.fakes import GATEWAY_KEY
from tests.integration.live_client import (
    LIVE,
    Stream,
    action,
    build_app,
    cookie,
    csrf_of,
    log_in,
    running,
)
from tests.integration.panel_client import ADMIN_TOKEN

AUTH = {"Authorization": f"Bearer {GATEWAY_KEY}"}
CHAT = {"model": "m", "messages": [{"role": "user", "content": f"DNI {SENTINEL_DNI}"}]}


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _event(origin: Origin = Origin.OPENAI_CHAT) -> LiveEvent:
    return LiveEvent(
        origin=origin,
        client=Client.CURL,
        outcome=Outcome.MASKED,
        reason=LiveReason.NONE,
        provider=Provider.OPENAI,
        at=0,
        types={EntityType.ES_DNI: 1},
    )


@pytest.fixture
def app() -> FastAPI:
    return build_app()


# --- Connecting -----------------------------------------------------------------------------------


async def test_a_live_session_gets_a_snapshot_with_the_doors_headers(app: FastAPI) -> None:
    async with running(app) as client:
        session_id = await log_in(client)
        async with Stream(app, session_id) as stream:
            assert stream.status == 200
            (content_type,) = stream.header("content-type")
            assert content_type.split(";")[0] == "text/event-stream"
            assert stream.header("x-accel-buffering") == ["no"]
            assert stream.header("cache-control") == [NO_STORE]
            for name, value in SECURITY_HEADERS:
                if name.lower() != "cache-control":
                    assert stream.header(name) == [value], name
            assert stream.header("content-length") == []
            name, data = await stream.event()
            assert name == "snapshot"
            assert data == {
                "recent": [],
                "hidden": [],
                "outcomes": [{"outcome": outcome.value, "count": 0} for outcome in Outcome],
                "checks": 0,
                "uptime_seconds": data["uptime_seconds"],
                "processes": 1,
            }
            assert type(data["uptime_seconds"]) is int


async def test_each_proxy_request_arrives_as_a_request_event(app: FastAPI) -> None:
    async with running(app) as client:
        session_id = await log_in(client)
        async with Stream(app, session_id) as stream:
            await stream.event()
            headers = {**AUTH, "User-Agent": "curl/8.9.1"}
            response = await client.post("/v1/chat/completions", json=CHAT, headers=headers)
            assert response.status_code == 200

            name, data = await stream.event()
            assert name == "request"
            assert data == {
                "origin": "openai_chat",
                "client": "curl",
                "outcome": "masked",
                "reason": "none",
                "provider": "openai",
                "at": data["at"],
                "types": [{"type": "ES_DNI", "count": 1}],
            }
            assert data["at"] % 60 == 0


async def test_a_reconnect_gets_the_recent_events_and_totals_in_its_snapshot(app: FastAPI) -> None:
    async with running(app) as client:
        session_id = await log_in(client)
        for _ in range(RECENT + 3):
            await client.post("/v1/chat/completions", json=CHAT, headers=AUTH)
        await client.post("/antifaz/scan", json={"text": SENTINEL_DNI}, headers=AUTH)
        async with Stream(app, session_id) as stream:
            name, data = await stream.event()
    assert name == "snapshot"
    assert len(data["recent"]) == RECENT
    assert data["recent"][0]["origin"] == "scan"  # newest first
    assert data["hidden"] == [{"type": "ES_DNI", "count": RECENT + 3}]
    assert {"outcome": "masked", "count": RECENT + 3} in data["outcomes"]
    assert data["checks"] == 1


@pytest.mark.parametrize("accept", [None, "application/json", "*/*", "text/html", "text/event"])
async def test_without_accept_text_event_stream_it_is_406(app: FastAPI, accept: str | None) -> None:
    async with running(app) as client:
        session_id = await log_in(client)
        headers = {"Cookie": cookie(session_id), "Sec-Fetch-Site": "same-origin"}
        if accept is not None:
            headers["Accept"] = accept
        response = await client.get(LIVE, headers=headers)
    assert response.status_code == 406
    assert response.json()["error"]["code"] == "not_acceptable"
    assert response.headers["cache-control"] == NO_STORE


async def test_an_accept_list_with_text_event_stream_is_accepted(app: FastAPI) -> None:
    async with running(app) as client:
        session_id = await log_in(client)
        accept = {"Accept": "application/json, text/event-stream;q=0.9"}
        async with Stream(app, session_id, accept) as stream:
            assert stream.status == 200
            assert (await stream.event())[0] == "snapshot"


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "HEAD"])
async def test_other_methods_are_405(app: FastAPI, method: str) -> None:
    async with running(app) as client:
        session_id = await log_in(client)
        token = await csrf_of(client, session_id)
        response = await client.request(
            method,
            LIVE,
            content=b"{}",
            headers={**action(session_id, token), "Accept": "text/event-stream"},
        )
    assert response.status_code == 405


async def test_without_a_session_it_is_401_and_nothing_streams(app: FastAPI) -> None:
    async with running(app):
        stream = Stream(app, None)
        async with stream:
            assert stream.status == 401
            assert await stream.closed()
        assert b"event:" not in stream.raw


# --- Watching is not a use of the session ---------------------------------------------------------


def test_the_live_stream_is_the_only_request_that_is_never_a_use() -> None:
    from antifaz.web.door import NOT_A_USE

    assert frozenset({(LIVE, "GET")}) == NOT_A_USE


async def test_connecting_and_reconnecting_never_refreshes_the_last_use(app: FastAPI) -> None:
    clock = Clock()
    app.state.panel_state.sessions._clock = clock
    async with running(app) as client:
        session_id = await log_in(client)
        key = fingerprint(session_id)
        store = app.state.panel_state.sessions
        for _ in range(3):
            clock.now += 300
            async with Stream(app, session_id) as stream:
                await stream.event()
            assert store._sessions[key].last_used == 1000.0
        # The control: any other request of the panel's own page is a use.
        await client.get(
            "/panel/api/status",
            headers={"Cookie": cookie(session_id), "Sec-Fetch-Site": "same-origin"},
        )
        assert store._sessions[key].last_used == 1900.0


# --- How a stream ends ----------------------------------------------------------------------------


async def _end(stream: Stream) -> str:
    """Skip to the `end` event and return its reason; the stream must close right after."""
    while True:
        name, data = await stream.event()
        if name == "end":
            assert list(data) == ["reason"]
            assert await stream.closed()
            assert stream.finished
            reason: str = data["reason"]
            return reason


async def test_logout_ends_the_stream_at_once(app: FastAPI) -> None:
    async with running(app) as client:
        session_id = await log_in(client)
        token = await csrf_of(client, session_id)
        async with Stream(app, session_id) as stream:
            await stream.event()
            response = await client.post(
                "/panel/logout", content=b"{}", headers=action(session_id, token)
            )
            assert response.status_code == 200
            assert await _end(stream) == "logout"
            await client.post("/v1/chat/completions", json=CHAT, headers=AUTH)
            assert b"event: request" not in stream.raw  # nothing more arrives


async def test_a_new_login_with_the_old_cookie_ends_its_streams_as_replaced(app: FastAPI) -> None:
    async with running(app) as client:
        session_id = await log_in(client)
        async with Stream(app, session_id) as stream:
            await stream.event()
            response = await client.post(
                "/panel/login",
                content=json.dumps({"token": ADMIN_TOKEN}).encode(),
                headers={
                    "Cookie": cookie(session_id),
                    "Sec-Fetch-Site": "same-origin",
                    "Origin": "https://antifaz.example",
                    "Content-Type": "application/json",
                },
            )
            assert response.status_code == 200
            assert await _end(stream) == "replaced"


async def test_the_stream_ends_after_its_maximum_age(app: FastAPI) -> None:
    app.state.live_streams.max_seconds = 0.3
    async with running(app) as client:
        session_id = await log_in(client)
        async with Stream(app, session_id) as stream:
            assert (await stream.event())[0] == "snapshot"
            assert await _end(stream) == "max_age"
    assert MAX_STREAM_SECONDS == 30 * 60


async def test_at_the_session_deadline_an_expired_session_ends_the_stream(app: FastAPI) -> None:
    clock = Clock()
    app.state.panel_state.sessions._clock = clock
    async with running(app) as client:
        session_id = await log_in(client)
        clock.now += IDLE_SECONDS - 0.3  # 0.3 s of the session's own time left
        async with Stream(app, session_id) as stream:
            await stream.event()
            clock.now += 1  # by the store's clock the session is over now
            assert await _end(stream) == "expired"


async def test_seguir_extends_the_session_and_the_stream_goes_on(app: FastAPI) -> None:
    clock = Clock()
    app.state.panel_state.sessions._clock = clock
    async with running(app) as client:
        session_id = await log_in(client)
        token = await csrf_of(client, session_id)  # GET /panel: a use, at 1000
        clock.now += IDLE_SECONDS - 0.3
        async with Stream(app, session_id) as stream:
            await stream.event()
            response = await client.post(
                "/panel/api/session", content=b"{}", headers=action(session_id, token)
            )
            assert response.status_code == 200  # «Seguir»: a use, the session has 30 min again
            assert await stream.nothing_for(0.8)  # past the old deadline: still open
            await client.post("/panel/logout", content=b"{}", headers=action(session_id, token))
            assert await _end(stream) == "logout"


async def test_shutdown_ends_every_open_stream() -> None:
    app = build_app()
    async with running(app) as client:
        session_id = await log_in(client)
        stream = Stream(app, session_id)
        await stream.__aenter__()
        await stream.event()
    # The lifespan has ended: the stream was told and closed.
    assert await _end(stream) == "shutdown"


# --- Limits ---------------------------------------------------------------------------------------


async def test_a_third_stream_of_one_session_is_busy(app: FastAPI) -> None:
    async with running(app) as client:
        session_id = await log_in(client)
        async with Stream(app, session_id) as one, Stream(app, session_id) as two:
            await one.event()
            await two.event()
            async with Stream(app, session_id) as three:
                assert three.status == 200
                assert await _end(three) == "busy"
                assert b"event: snapshot" not in three.raw
            assert await one.nothing_for(0.1) and await two.nothing_for(0.1)
        assert MAX_PER_SESSION == 2


async def test_a_sixth_stream_of_the_process_is_busy(app: FastAPI) -> None:
    async with running(app) as client:
        sessions = [await log_in(client) for _ in range(3)]
        owners = [sessions[0], sessions[0], sessions[1], sessions[1], sessions[2]]
        streams = [Stream(app, owner) for owner in owners]
        for stream in streams:
            await stream.__aenter__()
            await stream.event()
        async with Stream(app, sessions[2]) as sixth:
            assert await _end(sixth) == "busy"
        streams[0].disconnect()
        assert await streams[0].closed()
        async with Stream(app, sessions[2]) as again:  # a place is free again
            assert (await again.event())[0] == "snapshot"
        for stream in streams[1:]:
            await stream.__aexit__()
    assert MAX_STREAMS == 5


async def test_a_full_queue_sends_a_fresh_snapshot_instead(app: FastAPI) -> None:
    async with running(app) as client:
        session_id = await log_in(client)
        feed = app.state.live_feed
        async with Stream(app, session_id) as stream:
            await stream.event()
            for _ in range(SUBSCRIBER_QUEUE + 8):  # in one go: the stream cannot keep up
                feed.record(_event())
            name, data = await stream.event()
            assert name == "snapshot"
            assert len(data["recent"]) == RECENT
            assert await stream.nothing_for(0.2)
            feed.record(_event())
            assert (await stream.event())[0] == "request"
        assert b"retry:" not in stream.raw


async def test_heartbeats_are_comments_only_while_a_stream_is_open(app: FastAPI) -> None:
    app.state.live_streams.heartbeat_seconds = 0.1
    async with running(app) as client:
        session_id = await log_in(client)
        async with Stream(app, session_id) as stream:
            await stream.event()
            assert (await stream.frame()).startswith(":")
            assert (await stream.frame()).startswith(":")
    assert b"retry:" not in stream.raw
    assert all(
        line == b"" or line.startswith((b"event: ", b"data: ", b":"))
        for line in stream.raw.split(b"\n")
    )


def _ours(loop: asyncio.AbstractEventLoop) -> list[Any]:
    """The timers scheduled on the loop that are still pending."""
    return [handle for handle in loop._scheduled if not handle.cancelled()]  # type: ignore[attr-defined]


async def test_with_no_stream_open_there_is_no_task_and_no_timer(app: FastAPI) -> None:
    loop = asyncio.get_running_loop()
    async with running(app) as client:
        session_id = await log_in(client)
        await asyncio.sleep(0)
        tasks, timers = len(asyncio.all_tasks()), len(_ours(loop))
        async with Stream(app, session_id) as stream:
            await stream.event()
            assert len(_ours(loop)) > timers  # an open stream waits with a timer
        await asyncio.sleep(0)
        assert app.state.live_streams.open_streams == 0
        assert app.state.live_feed._subscribers == []
        for _ in range(3):
            await client.post("/v1/chat/completions", json=CHAT, headers=AUTH)
        await asyncio.sleep(0)
        assert len(asyncio.all_tasks()) == tasks
        assert len(_ours(loop)) == timers


# --- The schema: every leaf is an enum value or an integer ----------------------------------------

_ENUM_VALUES = (
    {member.value for member in EntityType}
    | {member.value for member in Origin}
    | {member.value for member in Client}
    | {member.value for member in Outcome}
    | {member.value for member in LiveReason}
    | {member.value for member in Provider}
    | {"logout", "expired", "replaced", "busy", "max_age", "shutdown"}
)


def _leaves(node: Any) -> list[Any]:
    if isinstance(node, dict):
        return [leaf for value in node.values() for leaf in _leaves(value)]
    if isinstance(node, list):
        return [leaf for value in node for leaf in _leaves(value)]
    return [node]


async def test_every_json_leaf_of_every_event_is_an_enum_value_or_an_integer(app: FastAPI) -> None:
    async with running(app) as client:
        session_id = await log_in(client)
        token = await csrf_of(client, session_id)
        await client.post("/v1/chat/completions", json=CHAT, headers=AUTH)
        async with Stream(app, session_id) as stream:
            events = [await stream.event()]
            await client.post("/antifaz/scan", json={"text": SENTINEL_DNI}, headers=AUTH)
            events.append(await stream.event())
            await client.post(
                "/panel/api/try",
                content=json.dumps({"text": SENTINEL_DNI, "lang": "es"}).encode(),
                headers=action(session_id, token),
            )
            events.append(await stream.event())
            await client.post("/panel/logout", content=b"{}", headers=action(session_id, token))
            events.append(await stream.event())
    assert [name for name, _ in events] == ["snapshot", "request", "request", "end"]
    leaves = [leaf for _, data in events for leaf in _leaves(data)]
    assert leaves
    for leaf in leaves:
        assert type(leaf) is int or (type(leaf) is str and leaf in _ENUM_VALUES), leaf
