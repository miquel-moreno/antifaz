"""HTTP middleware: attach our own request id to every request, log and response."""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from antifaz.logging import request_id_var

logger = logging.getLogger("http")

REQUEST_ID_HEADER = "X-Request-ID"
_KNOWN_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # Always our own id: the client's X-Request-ID is ignored, so nothing it writes (a DNI, a
    # key) can reach the logs or the response headers through it.
    request_id = uuid.uuid4().hex
    token = request_id_var.set(request_id)
    start = time.perf_counter()
    try:
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        # Only a registered route path is logged: any other path is client text (a key or a
        # DNI pasted in the URL would end up in the log).
        path = request.scope["path"]
        logger.info(
            "%s %s -> %s (%.1f ms)",
            request.method if request.method in _KNOWN_METHODS else "-",
            path if path in request.app.state.route_paths else "-",
            response.status_code,
            elapsed_ms,
        )
    finally:
        request_id_var.reset(token)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response
