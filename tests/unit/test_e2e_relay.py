"""The e2e relay (tests/e2e/relay.py): fixed targets inside the internal subnet, bounded.

Its routes are IP literals inside the internal network's subnet (no name is looked up at
connection time); it copies bytes both ways and passes the end of stream on; it gives up on a
target that does not answer, on idle connections and beyond a number of connections. These
tests run it on 127.0.0.1 with two local asyncio servers: no Docker, no network.
"""

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from tests.e2e import relay

SUBNET = "10.213.47.0/24"

Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


def test_parse_routes_reads_ip_targets_inside_the_subnet() -> None:
    routes = relay.parse_routes("8000=10.213.47.10:8000, 9000=10.213.47.11:9000", SUBNET)
    assert routes == [(8000, "10.213.47.10", 8000), (9000, "10.213.47.11", 9000)]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "8000",
        "8000=",
        "8000=antifaz:8000",  # a name would be looked up at connection time
        "8000=10.213.48.10:8000",  # outside the internal subnet
        "8000=1.1.1.1:443",
        "8000=[::1]:8000",
        "8000=10.213.47.10",
        "8000=10.213.47.10:x",
        "8000=10.213.47.10:0",
        "8000=10.213.47.10:65536",
        "0=10.213.47.10:8000",
        "x=10.213.47.10:8000",
        "8000=10.213.47.10:8000,",
        "8000=10.213.47.10:8000,8000=10.213.47.11:9000",  # the same port twice
    ],
)
def test_parse_routes_refuses_anything_else(text: str) -> None:
    with pytest.raises(ValueError, match="E2E_RELAY_ROUTES"):
        relay.parse_routes(text, SUBNET)


def test_parse_routes_needs_a_subnet() -> None:
    with pytest.raises(ValueError):
        relay.parse_routes("8000=10.213.47.10:8000", "not-a-subnet")


async def _server(handler: Handler) -> tuple[asyncio.Server, int]:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port: int = server.sockets[0].getsockname()[1]
    return server, port


async def _relay_to(port: int, **limits: float) -> tuple[list[asyncio.Server], int]:
    servers = await relay.start([(0, "127.0.0.1", port)], bind="127.0.0.1", **limits)
    listen: int = servers[0].sockets[0].getsockname()[1]
    return servers, listen


async def _close(*servers: asyncio.Server) -> None:
    for server in servers:
        server.close()
        await server.wait_closed()


async def _reply_after_eof(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    data = await reader.read()  # until the client's end of stream comes through the relay
    writer.write(b"got:" + data)
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def _echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while data := await reader.read(1024):
        writer.write(data)
        await writer.drain()
    writer.close()
    await writer.wait_closed()


async def _hang(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    await reader.read()
    writer.close()


def test_bytes_go_both_ways_and_the_end_of_stream_is_passed_on() -> None:
    async def scenario() -> bytes:
        target, port = await _server(_reply_after_eof)
        servers, listen = await _relay_to(port)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", listen)
            writer.write(b"hello")
            await writer.drain()
            writer.write_eof()
            answer = await asyncio.wait_for(reader.read(), 5)
            writer.close()
            return answer
        finally:
            await _close(*servers, target)

    assert asyncio.run(scenario()) == b"got:hello"


def test_an_echo_comes_back_before_the_end_of_stream() -> None:
    async def scenario() -> bytes:
        target, port = await _server(_echo)
        servers, listen = await _relay_to(port)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", listen)
            writer.write(b"ping")
            await writer.drain()
            answer = await asyncio.wait_for(reader.readexactly(4), 5)
            writer.close()
            return answer
        finally:
            await _close(*servers, target)

    assert asyncio.run(scenario()) == b"ping"


def test_the_client_is_closed_when_the_target_is_down() -> None:
    async def scenario() -> bytes:
        target, port = await _server(_echo)
        await _close(target)  # nothing listens on that port any more
        servers, listen = await _relay_to(port)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", listen)
            answer = await asyncio.wait_for(reader.read(), 10)
            writer.close()
            return answer
        finally:
            await _close(*servers)

    assert asyncio.run(scenario()) == b""


def test_idle_connections_are_closed() -> None:
    async def scenario() -> bytes:
        target, port = await _server(_echo)
        servers, listen = await _relay_to(port, idle_timeout=0.3)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", listen)
            answer = await asyncio.wait_for(reader.read(), 5)  # nothing sent: closed when idle
            writer.close()
            return answer
        finally:
            await _close(*servers, target)

    assert asyncio.run(scenario()) == b""


def test_connections_beyond_the_limit_are_closed_at_once() -> None:
    async def scenario() -> tuple[bytes, bytes]:
        target, port = await _server(_echo)
        servers, listen = await _relay_to(port, max_connections=1)
        try:
            r1, w1 = await asyncio.open_connection("127.0.0.1", listen)
            w1.write(b"one")
            await w1.drain()
            first = await asyncio.wait_for(r1.readexactly(3), 5)
            r2, w2 = await asyncio.open_connection("127.0.0.1", listen)
            second = await asyncio.wait_for(r2.read(), 5)  # refused: closed with no bytes
            w1.close()
            w2.close()
            return first, second
        finally:
            await _close(*servers, target)

    assert asyncio.run(scenario()) == (b"one", b"")


def test_the_limits_have_safe_defaults() -> None:
    assert relay.CONNECT_TIMEOUT == 5
    assert relay.MAX_CONNECTIONS == 64
    assert 0 < relay.IDLE_TIMEOUT <= 300
