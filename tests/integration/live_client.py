"""An in-process client for the panel's SSE stream (issue 53, PR 11e-1).

TestClient and httpx's ASGITransport wait for the whole answer, and a live stream does not end
by itself: this drives the ASGI app directly, on the test's own event loop, so a test can read
events one by one while it logs out, extends the session or sends proxy requests (through an
httpx.AsyncClient over ASGITransport, same app, same loop) and can disconnect like a browser
closing the tab. The gateway's lifespan runs around it (`running`).

Nothing here touches the network: the provider is a MockTransport fake, as everywhere else.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

from antifaz.api.app import create_app
from tests.integration.fakes import FakeUpstream
from tests.integration.panel_client import (
    ADMIN_TOKEN,
    HTTPS_URL,
    LOCAL_COOKIE,
    SECURE_COOKIE,
    panel_settings,
)
from tests.redteam.conftest import both_echo

LIVE = "/panel/api/live"
HOST = "antifaz.example"
ORIGIN = HTTPS_URL
STEP = 2.0  # the most a test waits for one thing to happen


def build_app(**kwargs: Any) -> FastAPI:
    http = httpx.AsyncClient(transport=httpx.MockTransport(FakeUpstream(both_echo)))
    return create_app(kwargs.pop("settings", None) or panel_settings(), http_client=http, **kwargs)


@asynccontextmanager
async def running(app: FastAPI, base_url: str = HTTPS_URL) -> AsyncIterator[httpx.AsyncClient]:
    """The app's lifespan around an AsyncClient for plain (finite) requests."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
            yield client


def same_origin(**extra: str) -> dict[str, str]:
    return {"Sec-Fetch-Site": "same-origin", "Origin": ORIGIN, **extra}


async def log_in(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/panel/login",
        content=json.dumps({"token": ADMIN_TOKEN}).encode(),
        headers={
            "Sec-Fetch-Site": "same-origin",
            "Origin": f"{client.base_url.scheme}://{client.base_url.netloc.decode()}",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200, response.text
    client.cookies.clear()
    value: str = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    return value


def cookie(session_id: str) -> str:
    return f"{SECURE_COOKIE}={session_id}"


async def csrf_of(client: httpx.AsyncClient, session_id: str) -> str:
    page = await client.get("/panel", headers={"Cookie": cookie(session_id)})
    assert page.status_code == 200
    return page.text.split('name="antifaz-csrf" content="', 1)[1].split('"', 1)[0]


def action(session_id: str, token: str) -> dict[str, str]:
    return same_origin(
        **{
            "Cookie": cookie(session_id),
            "X-Antifaz-CSRF": token,
            "Content-Type": "application/json",
        }
    )


class Stream:
    """One GET of the live stream, read frame by frame."""

    def __init__(
        self,
        app: FastAPI,
        session_id: str | None,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        scheme: str = "https",
        host: str = HOST,
        stall_after: int | None = None,
    ) -> None:
        """`session_id` goes in the cookie of `scheme` (none if None); `headers` add to or
        replace the browser's (an empty value drops one). `stall_after`: after that many body
        messages the client stops reading (every later send waits forever, like a full socket
        of a client that went away without closing), and never disconnects by itself."""
        sent = {"Accept": "text/event-stream", "Sec-Fetch-Site": "same-origin", "Host": host}
        if session_id is not None:
            name = SECURE_COOKIE if scheme == "https" else LOCAL_COOKIE
            sent["Cookie"] = f"{name}={session_id}"
        sent.update(headers or {})
        self._scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": scheme,
            "path": LIVE,
            "raw_path": LIVE.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [
                (name.lower().encode("latin-1"), value.encode("latin-1"))
                for name, value in sent.items()
                if value
            ],
            "client": ("127.0.0.1", 50001),
            "server": (host, 443 if scheme == "https" else 80),
            "state": {},
        }
        self._app = app
        self._gone = asyncio.Event()
        self._started = asyncio.Event()
        self._finished = asyncio.Event()
        self._more = asyncio.Event()
        self.status: int | None = None
        self.headers: list[tuple[bytes, bytes]] = []
        self.raw = b""
        self._read = 0
        self._task: asyncio.Task[None] | None = None
        self._asked = False
        self._stall_after = stall_after
        self._bodies = 0
        self.error: BaseException | None = None

    async def _receive(self) -> dict[str, Any]:
        if not self._asked:
            self._asked = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._gone.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self.headers = list(message.get("headers", []))
            self._started.set()
        elif message["type"] == "http.response.body":
            if self._stall_after is not None and self._bodies >= self._stall_after:
                await asyncio.Event().wait()  # never read: only a cancellation ends this send
            self._bodies += 1
            self.raw += message.get("body", b"")
            self._more.set()
            if not message.get("more_body", False):
                self._finished.set()

    async def __aenter__(self) -> "Stream":
        self._task = asyncio.create_task(self._app(self._scope, self._receive, self._send))
        await asyncio.wait_for(self._started.wait(), STEP)
        return self

    async def __aexit__(self, *_: object) -> None:
        self.disconnect()
        await self.closed()

    def header(self, name: str) -> list[str]:
        key = name.lower().encode()
        return [value.decode("latin-1") for header, value in self.headers if header == key]

    def disconnect(self) -> None:
        self._gone.set()

    async def closed(self, timeout: float = STEP) -> bool:
        """Whether the app finished the answer and returned within `timeout`."""
        assert self._task is not None
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout)
        except TimeoutError:
            return False
        except Exception as error:  # the app raised (after the answer started): it did return
            self.error = error
        return True

    @property
    def finished(self) -> bool:
        return self._finished.is_set()

    async def frame(self, timeout: float = STEP) -> str:
        """The next whole frame (up to the blank line), as text."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while b"\n\n" not in self.raw[self._read :]:
            if self._finished.is_set():
                raise EOFError("the stream ended")
            self._more.clear()
            left = deadline - loop.time()
            if left <= 0:
                raise TimeoutError("no frame in time")
            try:
                await asyncio.wait_for(self._more.wait(), left)
            except TimeoutError:
                raise TimeoutError("no frame in time") from None
        end = self.raw.index(b"\n\n", self._read)
        text = self.raw[self._read : end].decode("utf-8")
        self._read = end + 2
        return text

    async def event(self, timeout: float = STEP) -> tuple[str, Any]:
        """The next event (name, JSON data), skipping comment frames (heartbeats)."""
        while True:
            text = await self.frame(timeout)
            if text.startswith(":"):
                continue
            lines = text.split("\n")
            assert len(lines) == 2, text
            name, data = lines
            assert name.startswith("event: ") and data.startswith("data: "), text
            return name.removeprefix("event: "), json.loads(data.removeprefix("data: "))

    async def nothing_for(self, seconds: float) -> bool:
        """True if no event arrives within `seconds` and the stream is still open."""
        try:
            await self.event(seconds)
        except TimeoutError:
            return not self.finished
        return False
