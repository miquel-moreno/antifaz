"""The e2e relay: the only door from the host to the stack on the internal network.

A network with `internal: true` has no way out, and Docker does not publish its ports to the
host either. This relay is on both networks: the internal one (where the gateway and the fake
provider live) and a normal one, where Compose publishes its ports on 127.0.0.1 only. Each
route in E2E_RELAY_ROUTES ("listen=host:port,...") copies TCP bytes from the port it listens
on to one fixed target on the internal network. It never opens a connection the client
chooses, so it is not a proxy the gateway could use to go out: the gateway has no route to it
that leads anywhere but back to itself or the fake provider.

`relay.py --check` (the healthcheck) connects to every listening port. Standard library only:
it runs with the Antifaz image's own python, like the fake provider.
"""

import asyncio
import os
import socket
import sys

Route = tuple[int, str, int]


def parse_routes(text: str) -> list[Route]:
    routes = []
    for item in text.split(","):
        listen, _, target = item.strip().partition("=")
        host, _, port = target.rpartition(":")
        routes.append((int(listen), host, int(port)))
    return routes


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            if writer.can_write_eof():
                writer.write_eof()
        except (ConnectionError, OSError):
            writer.close()


async def _serve(route: Route) -> asyncio.Server:
    listen, host, port = route

    async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        try:
            target_r, target_w = await asyncio.open_connection(host, port)
        except OSError:
            client_w.close()
            return
        await asyncio.gather(_pipe(client_r, target_w), _pipe(target_r, client_w))
        for writer in (client_w, target_w):
            writer.close()

    return await asyncio.start_server(handle, "0.0.0.0", listen)  # noqa: S104 - its own container


async def _main(routes: list[Route]) -> None:
    servers = [await _serve(route) for route in routes]
    await asyncio.gather(*(server.serve_forever() for server in servers))


def _check(routes: list[Route]) -> int:
    for listen, _, _ in routes:
        with socket.create_connection(("127.0.0.1", listen), timeout=2):
            pass
    return 0


if __name__ == "__main__":
    ROUTES = parse_routes(os.environ["E2E_RELAY_ROUTES"])
    if sys.argv[1:] == ["--check"]:
        sys.exit(_check(ROUTES))
    asyncio.run(_main(ROUTES))
