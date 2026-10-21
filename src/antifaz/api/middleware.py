"""HTTP middleware: attach our own request id to every request, log and response.

A plain ASGI middleware (11e-1 review), not Starlette's BaseHTTPMiddleware: that one relays
every answer through an in-memory stream in a task of its own, so a stalled client kept that
task waiting forever, out of reach of «En directo»'s write timeout (web/live_stream.py). Here
every message goes straight to the server's `send`, so a deadline on a send really drops the
connection.

The access line is written when the answer starts (status known), as before.
"""

import logging
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from antifaz.logging import request_id_var

logger = logging.getLogger("http")

REQUEST_ID_HEADER = "X-Request-ID"
_KNOWN_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Always our own id: the client's X-Request-ID is ignored, so nothing it writes (a DNI, a
        # key) can reach the logs or the response headers through it.
        request_id = uuid.uuid4().hex
        token = request_id_var.set(request_id)
        start = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                _log(scope, message["status"], (time.perf_counter() - start) * 1000)
                message = {**message, "headers": list(message.get("headers", []))}
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                # Every answer declares its type (JSON, SSE, or the panel's HTML): a browser must
                # never guess another one from a body (an HTML page out of a JSON error).
                headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)


def _log(scope: Scope, status: int, elapsed_ms: float) -> None:
    # Only a registered route path is logged: any other path is client text (a key or a DNI
    # pasted in the URL would end up in the log).
    method = scope["method"]
    path = scope["path"]
    logger.info(
        "%s %s -> %s (%.1f ms)",
        method if method in _KNOWN_METHODS else "-",
        path if path in scope["app"].state.route_paths else "-",
        status,
        elapsed_ms,
    )
