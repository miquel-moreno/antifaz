"""The e2e relay: the only door from the host to the stack on the internal network.

A network with `internal: true` has no way out, and Docker does not publish its ports to the
host either. This relay is on both networks: the internal one (where the gateway and the fake
provider live, at fixed addresses) and a normal one, where Compose publishes its ports on
127.0.0.1 only. Each route in E2E_RELAY_ROUTES ("listen=IP:port,...") copies TCP bytes from
the port it listens on to one fixed IP inside E2E_RELAY_SUBNET (the internal network's subnet):
no name is looked up, a target outside the subnet is refused at start, and it never opens a
connection the client chooses, so it gives the gateway no way out.

Bounded: 5 s to connect to the target, at most 64 connections at once (more are closed at
once), and a connection with no bytes either way for IDLE_TIMEOUT seconds is closed.

`relay.py --check` (the healthcheck) connects to every listening port. Standard library only:
it runs with the Antifaz image's own python, like the fake provider.
"""

import asyncio
import contextlib
import ipaddress
import os
import socket
import sys
from collections.abc import Awaitable, Callable

Route = tuple[int, str, int]
Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]

CONNECT_TIMEOUT = 5
MAX_CONNECTIONS = 64
IDLE_TIMEOUT = 120


def _port(text: str) -> int:
    if not text.isdigit() or not 1 <= int(text) <= 65535:
        raise ValueError
    return int(text)


def parse_routes(text: str, subnet: str) -> list[Route]:
    """`listen=IP:port,...` with every IP an IPv4 literal inside `subnet`."""
    network = ipaddress.ip_network(subnet)
    routes: list[Route] = []
    for item in text.split(","):
        try:
            listen, equals, target = item.strip().partition("=")
            host, colon, port = target.rpartition(":")
            if not equals or not colon:
                raise ValueError
            address = ipaddress.ip_address(host)
            if address.version != 4 or address not in network:
                raise ValueError
            route = (_port(listen), str(address), _port(port))
        except ValueError:
            raise ValueError(f"E2E_RELAY_ROUTES: bad route {item.strip()!r}") from None
        if route[0] in {r[0] for r in routes}:
            raise ValueError(f"E2E_RELAY_ROUTES: port {route[0]} twice")
        routes.append(route)
    return routes


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(ConnectionError, OSError):
        await writer.wait_closed()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, idle: float) -> None:
    """Copy until end of stream (then pass it on) or until nothing comes for `idle` s."""
    try:
        while data := await asyncio.wait_for(reader.read(65536), idle):
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (TimeoutError, ConnectionError, OSError):
        await _close(writer)


async def start(
    routes: list[Route],
    *,
    bind: str = "0.0.0.0",  # noqa: S104 - its own container; Compose publishes on 127.0.0.1
    connect_timeout: float = CONNECT_TIMEOUT,
    max_connections: int = MAX_CONNECTIONS,
    idle_timeout: float = IDLE_TIMEOUT,
) -> list[asyncio.Server]:
    """One listening server per route, sharing the connection limit."""
    active = 0

    def handler(host: str, port: int) -> Handler:
        async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
            nonlocal active
            if active >= max_connections:
                await _close(client_w)
                return
            active += 1
            try:
                try:
                    target_r, target_w = await asyncio.wait_for(
                        asyncio.open_connection(host, port), connect_timeout
                    )
                except (TimeoutError, OSError):
                    return
                await asyncio.gather(
                    _pipe(client_r, target_w, idle_timeout),
                    _pipe(target_r, client_w, idle_timeout),
                )
                await _close(target_w)
            finally:
                await _close(client_w)
                active -= 1

        return handle

    return [
        await asyncio.start_server(handler(host, port), bind, listen)
        for listen, host, port in routes
    ]


async def _main(routes: list[Route]) -> None:
    servers = await start(routes)
    await asyncio.gather(*(server.serve_forever() for server in servers))


def _check(routes: list[Route]) -> int:
    for listen, _, _ in routes:
        with socket.create_connection(("127.0.0.1", listen), timeout=2):
            pass
    return 0


if __name__ == "__main__":
    ROUTES = parse_routes(os.environ["E2E_RELAY_ROUTES"], os.environ["E2E_RELAY_SUBNET"])
    if sys.argv[1:] == ["--check"]:
        sys.exit(_check(ROUTES))
    asyncio.run(_main(ROUTES))
