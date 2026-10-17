"""The gateway of the browser tests (PR 11c-2a): in this process, on 127.0.0.1, never a provider.

- uvicorn in a daemon thread, on a socket this module binds to ("127.0.0.1", 0): a free port
  chosen by the system, never a fixed one, and never any address but loopback.
- Settings without any .env (`_env_file=None`) and with throwaway tokens made here
  (`secrets.token_hex(32)`): the API key and the admin token. The browser conftest removes every
  ANTIFAZ_*, OPENAI_* and ANTHROPIC_* variable for the session first.
- The providers' HTTP client is an httpx.MockTransport that records and raises on any use:
  nothing of the panel may reach a provider, and the fixture checks that nothing tried.
- What each browser really sent through the door on a POST (its Origin and Sec-Fetch-Site,
  and whether an X-Antifaz-CSRF header came, never its value) is recorded as it reaches the
  app (`door_headers`), so a test can say which headers a browser sends.
- Ready when uvicorn says it started (an Event set right after its startup), with a generous
  hang guard that only produces an error message: never a timing assertion.

Standard library, httpx, uvicorn and Antifaz only: no Playwright here.
"""

import asyncio
import secrets
import socket
import threading

import httpx
import uvicorn
from pydantic import SecretStr
from starlette.types import ASGIApp, Receive, Scope, Send

from antifaz.api.app import create_app
from antifaz.config import Settings

HOST = "127.0.0.1"
# Removed for the whole browser session (any case): Antifaz and provider settings, Playwright's
# own switches and debug output (DEBUG=pw:* logs every protocol message, tokens included), and
# proxies (the browsers must talk to 127.0.0.1 directly).
ISOLATED_PREFIXES = ("ANTIFAZ_", "OPENAI_", "ANTHROPIC_", "PLAYWRIGHT_")
ISOLATED_NAMES = frozenset(
    {"DEBUG", "DEBUG_FILE", "PWDEBUG", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
)


def isolated_variable(name: str) -> bool:
    """Whether the browser session removes this environment variable."""
    upper = name.upper()
    return upper in ISOLATED_NAMES or upper.startswith(ISOLATED_PREFIXES)


# Only a guard against a hang (a broken startup or shutdown), never a limit on speed.
HANG_GUARD_SECONDS = 120


class _NoProvider:
    """The providers' transport: records the attempt and fails it."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url.host}")
        raise AssertionError("the browser tests must never reach a provider")


class _PostRecorder:
    """ASGI wrapper: Origin, Sec-Fetch-Site and the presence of X-Antifaz-CSRF of every POST."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.posts: list[tuple[str, dict[str, str]]] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST":
            seen = {
                name.decode("latin-1"): value.decode("latin-1")
                for name, value in scope["headers"]
                if name in (b"origin", b"sec-fetch-site")
            }
            if any(name == b"x-antifaz-csrf" for name, _ in scope["headers"]):
                seen["x-antifaz-csrf"] = "present"  # never the value
            self.posts.append((scope["path"], seen))
        await self.app(scope, receive, send)


class _Server(uvicorn.Server):
    """uvicorn's server with an Event set once its startup ran (successful or not)."""

    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.ready = threading.Event()

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        try:
            await super().startup(sockets=sockets)
        finally:
            self.ready.set()


class PanelServer:
    """One gateway with the panel on, for one test. Start with `start()`, end with `stop()`."""

    def __init__(self) -> None:
        self.api_key = secrets.token_hex(32)
        self.admin_token = secrets.token_hex(32)
        self._provider = _NoProvider()
        settings = Settings(
            antifaz_api_key=SecretStr(self.api_key),
            admin_token=SecretStr(self.admin_token),
            allowed_hosts=[HOST, "localhost"],
            log_level="WARNING",
            _env_file=None,  # type: ignore[call-arg]
        )
        self._http = httpx.AsyncClient(transport=httpx.MockTransport(self._provider))
        self._recorder = _PostRecorder(create_app(settings, http_client=self._http))
        config = uvicorn.Config(
            self._recorder,
            loop="asyncio",
            lifespan="on",
            ws="none",
            access_log=False,
            log_level="warning",
            server_header=False,
        )
        self._server = _Server(config)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.bind((HOST, 0))
        self.port: int = self._socket.getsockname()[1]
        self.origin = f"http://{HOST}:{self.port}"
        self._thread = threading.Thread(target=self._run, name="panel-server", daemon=True)

    def door_headers(self, path: str) -> dict[str, str]:
        """What the last POST to `path` brought: origin, sec-fetch-site, x-antifaz-csrf."""
        sent = [headers for posted, headers in self._recorder.posts if posted == path]
        return sent[-1] if sent else {}

    @property
    def throwaway_tokens(self) -> tuple[str, str]:
        """The throwaway tokens of this gateway, to redact from any failure log."""
        return (self.api_key, self.admin_token)

    @property
    def provider_calls(self) -> list[str]:
        return list(self._provider.calls)

    def _run(self) -> None:
        try:
            self._server.run(sockets=[self._socket])
        finally:
            self._server.ready.set()  # a crash before startup must not leave start() waiting

    def start(self) -> None:
        self._thread.start()
        if not self._server.ready.wait(HANG_GUARD_SECONDS):
            raise RuntimeError("the panel test server did not start (hang guard)")
        if not self._server.started:
            raise RuntimeError("the panel test server failed to start; see the log above")

    def stop(self) -> None:
        """Stop the server (started or not) and release its socket and HTTP client."""
        self._server.should_exit = True
        try:
            if self._thread.ident is not None:
                self._thread.join(HANG_GUARD_SECONDS)
        finally:
            self._socket.close()
            if not self._http.is_closed:
                # In a thread of its own: Playwright's sync API keeps a loop running here.
                closer = threading.Thread(target=asyncio.run, args=(self._http.aclose(),))
                closer.start()
                closer.join(HANG_GUARD_SECONDS)
        if self._thread.is_alive():
            raise RuntimeError("the panel test server did not stop (hang guard)")

    @property
    def closed(self) -> bool:
        """The thread ended (or never started), the socket and the HTTP client are closed."""
        return not self._thread.is_alive() and self._socket.fileno() == -1 and self._http.is_closed
