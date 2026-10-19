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
- Options (11c-3b, `ServerOptions`): throwaway provider keys made here (they only make the
  Status view say "key configured"; the transport above still refuses every call) and a fake
  NER that only answers status() with a fixed state (no model, no process).
- Ready when uvicorn says it started (an Event set right after its startup), with a generous
  hang guard that only produces an error message: never a timing assertion.

Standard library, httpx, uvicorn and Antifaz only: no Playwright here.
"""

import asyncio
import http.server
import logging
import secrets
import socket
import threading
from dataclasses import dataclass

import httpx
import uvicorn
from pydantic import SecretStr
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
GRACEFUL_SHUTDOWN_SECONDS = 10


class _NoProvider:
    """The providers' transport: records the attempt and fails it."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url.host}")
        raise AssertionError("the browser tests must never reach a provider")


@dataclass(frozen=True)
class ServerOptions:
    """What a test's gateway has besides the defaults (11c-3b)."""

    provider_keys: bool = False  # throwaway OpenAI and Anthropic keys (never used: no provider)
    ner_state: str | None = None  # a fake NER answering this state; None: the NER is off


class FakeNer:
    """A NER that is never asked to detect: start, close and a fixed status()."""

    def __init__(self, state: str) -> None:
        self.state = state

    def start(self) -> None:
        pass

    def close(self) -> None:
        pass

    def status(self) -> str:
        return self.state


class _PostRecorder:
    """ASGI wrapper: Origin, Sec-Fetch-Site and the presence of X-Antifaz-CSRF of every POST,
    and the status the gateway answered it with (PR 11c-2b: a browser may hide the answer from
    the page and from Playwright, e.g. a no-cors answer blocked by its CORP header)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.posts: list[tuple[str, dict[str, str]]] = []
        self.statuses: list[tuple[str, int]] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        seen = {
            name.decode("latin-1"): value.decode("latin-1")
            for name, value in scope["headers"]
            if name in (b"origin", b"sec-fetch-site")
        }
        if any(name == b"x-antifaz-csrf" for name, _ in scope["headers"]):
            seen["x-antifaz-csrf"] = "present"  # never the value
        self.posts.append((scope["path"], seen))

        async def recording_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                self.statuses.append((scope["path"], message["status"]))
            await send(message)

        await self.app(scope, receive, recording_send)


SHUTDOWN_LIMIT_MESSAGE = "timeout graceful shutdown exceeded"


class _ShutdownLimitWatch(logging.Handler):
    """Notes when uvicorn had to cut connections at GRACEFUL_SHUTDOWN_SECONDS."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.hit = False

    def emit(self, record: logging.LogRecord) -> None:
        if SHUTDOWN_LIMIT_MESSAGE in record.getMessage():
            self.hit = True


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

    def __init__(self, options: ServerOptions | None = None) -> None:
        options = options or ServerOptions()
        self.api_key = secrets.token_hex(32)
        self.admin_token = secrets.token_hex(32)
        self.provider_keys = (
            (secrets.token_hex(32), secrets.token_hex(32)) if options.provider_keys else ()
        )
        self._provider = _NoProvider()
        keys = (
            {
                "openai_api_key": SecretStr(self.provider_keys[0]),
                "anthropic_api_key": SecretStr(self.provider_keys[1]),
            }
            if self.provider_keys
            else {}
        )
        settings = Settings(
            antifaz_api_key=SecretStr(self.api_key),
            admin_token=SecretStr(self.admin_token),
            allowed_hosts=[HOST, "localhost"],
            log_level="WARNING",
            _env_file=None,  # type: ignore[call-arg]
            **keys,  # type: ignore[arg-type]
        )
        self.ner = FakeNer(options.ner_state) if options.ner_state is not None else None
        self._http = httpx.AsyncClient(transport=httpx.MockTransport(self._provider))
        app = create_app(settings, http_client=self._http, ner=self.ner)  # type: ignore[arg-type]
        self._recorder = _PostRecorder(app)
        config = uvicorn.Config(
            self._recorder,
            loop="asyncio",
            lifespan="on",
            ws="none",
            access_log=False,
            log_level="warning",
            server_header=False,
            # A browser may keep a connection open whose request the door refused before
            # reading its body (the cross-site tests of 11c-2b): without a limit, the graceful
            # shutdown would wait for it forever. A hang guard, not a measure of speed.
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        )
        self._server = _Server(config)
        self._watch = _ShutdownLimitWatch()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.bind((HOST, 0))
        self.port: int = self._socket.getsockname()[1]
        self.origin = f"http://{HOST}:{self.port}"
        self._thread = threading.Thread(target=self._run, name="panel-server", daemon=True)

    def door_headers(self, path: str) -> dict[str, str]:
        """What the last POST to `path` brought: origin, sec-fetch-site, x-antifaz-csrf."""
        sent = [headers for posted, headers in self._recorder.posts if posted == path]
        return sent[-1] if sent else {}

    def door_statuses(self, path: str) -> list[int]:
        """The status of every POST to `path` the gateway answered, in order."""
        return [status for posted, status in self._recorder.statuses if posted == path]

    @property
    def throwaway_tokens(self) -> tuple[str, str]:
        """The throwaway tokens of this gateway, to redact from any failure log."""
        return (self.api_key, self.admin_token, *self.provider_keys)

    @property
    def provider_calls(self) -> list[str]:
        return list(self._provider.calls)

    def _run(self) -> None:
        try:
            self._server.run(sockets=[self._socket])
        finally:
            self._server.ready.set()  # a crash before startup must not leave start() waiting

    def start(self) -> None:
        logging.getLogger("uvicorn.error").addHandler(self._watch)
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
        logging.getLogger("uvicorn.error").removeHandler(self._watch)
        if self._thread.is_alive():
            raise RuntimeError("the panel test server did not stop (hang guard)")

    @property
    def hit_shutdown_limit(self) -> bool:
        """Whether stop() had to cut a connection at the graceful-shutdown limit."""
        return self._watch.hit

    @property
    def closed(self) -> bool:
        """The thread ended (or never started), the socket and the HTTP client are closed."""
        return not self._thread.is_alive() and self._socket.fileno() == -1 and self._http.is_closed


OTHER_SITE_PAGE = (
    b"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>another site</title>"
    b"<link rel='icon' href='data:,'></head><body><p>another site</p></body></html>"
)


class _OtherSiteHandler(http.server.BaseHTTPRequestHandler):
    """Every GET is the same plain page, without any CSP; anything else is a 501."""

    def do_GET(self) -> None:  # the name http.server calls
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(OTHER_SITE_PAGE)))
        self.end_headers()
        self.wfile.write(OTHER_SITE_PAGE)

    def log_message(self, format: str, *args: object) -> None:
        pass


class OtherSite:
    """Another origin for the cross-site tests (PR 11c-2b): a fixed page on a free loopback port.
    Reached by the name localhost, it is another site than the gateway's 127.0.0.1 (the session
    cookie, SameSite=Strict, does not travel); reached as 127.0.0.1 on its own port, it is the
    same site on another origin (the cookie travels). Both in the same loopback address space,
    so the browsers' local-network rules do not step in first. Standard library only; its page
    has no CSP, like an attacker's."""

    def __init__(self, name: str = "localhost") -> None:
        self._httpd = http.server.ThreadingHTTPServer((HOST, 0), _OtherSiteHandler)
        self._httpd.daemon_threads = True
        self.origin = f"http://{name}:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="other-site", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        if self._thread.ident is not None:
            self._httpd.shutdown()
            self._thread.join(HANG_GUARD_SECONDS)
        self._httpd.server_close()
