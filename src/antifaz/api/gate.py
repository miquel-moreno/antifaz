"""The gateway door (ADR-0015): one ASGI middleware in front of every route, closed by default.

Every path needs the Antifaz key except the exact paths in PUBLIC_PATHS. Then, on the same
requests: a browser `Origin` is refused unless configured (403) and a request that may carry a
body must be `application/json` in UTF-8 (415). There are no WebSocket routes: any WebSocket is
closed. The decision uses `scope["path"]`, the same decoded path the router uses.

Forbidden: registering security route by route. Deciding on `request.url` or a header the
client can rewrite (X-Forwarded-*). Echoing any header value in a response or a log.
"""

import hmac
from collections.abc import Iterable

from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from antifaz.api.errors import (
    AppError,
    OriginNotAllowedError,
    UnauthorizedError,
    UnsupportedMediaTypeError,
    error_body,
)
from antifaz.config import MIN_KEY_LENGTH

# Paths served without the key. Exact match only: "/healthz/" or "//healthz" need the key.
PUBLIC_PATHS = frozenset({"/healthz"})
# Methods that carry no body to process: the Content-Type rule does not apply.
BODILESS_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_JSON = "application/json"
_UTF8 = frozenset({"charset=utf-8", 'charset="utf-8"'})
_POLICY_VIOLATION = 1008  # WebSocket close code


def _header_values(scope: Scope) -> dict[bytes, list[bytes]]:
    """Every value of every header (names are lower case in ASGI). Repeats are kept."""
    values: dict[bytes, list[bytes]] = {}
    for name, value in scope["headers"]:
        values.setdefault(name, []).append(value)
    return values


def _is_json(values: list[bytes]) -> bool:
    """One Content-Type: application/json, optionally with charset=utf-8 and nothing else."""
    if len(values) != 1:
        return False
    media, *params = values[0].decode("latin-1").split(";")
    if media.strip().lower() != _JSON:
        return False
    return all(param.strip().lower() in _UTF8 for param in params)


class CaseInsensitiveTrustedHost(TrustedHostMiddleware):
    """Starlette's host check with the Host header lower-cased (host names have no case).

    It runs before the key check: a request for a host that is not ours (DNS rebinding) gets
    400 without reaching anything else. The configured hosts are lower-cased in the settings.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            scope["headers"] = [
                (name, value.lower() if name == b"host" else value)
                for name, value in scope["headers"]
            ]
        await super().__call__(scope, receive, send)


class GateMiddleware:
    def __init__(self, app: ASGIApp, *, api_key: str, allowed_origins: Iterable[str]) -> None:
        # Fails closed on its own, even if whoever builds it skipped check_safe_to_start().
        if len(api_key) < MIN_KEY_LENGTH:
            raise ValueError(f"the gateway key must have at least {MIN_KEY_LENGTH} characters")
        self.app = app
        self._key = api_key.encode()  # printable ASCII, checked at startup
        self._bearer = b"Bearer " + self._key
        self._origins = frozenset(origin.encode() for origin in allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            # No WebSocket routes: close before anything reaches the app, key or not.
            await send({"type": "websocket.close", "code": _POLICY_VIOLATION})
            return
        if scope["path"] in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        problem = self._problem(scope)
        if problem is not None:
            body = error_body(problem.code, problem.message)
            await JSONResponse(body, status_code=problem.status_code)(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _problem(self, scope: Scope) -> AppError | None:
        headers = _header_values(scope)
        if not self._authorized(headers):
            return UnauthorizedError()  # first: without the key, no hint about the other rules
        origins = headers.get(b"origin", [])
        if any(origin not in self._origins for origin in origins):
            return OriginNotAllowedError()
        if scope["method"] not in BODILESS_METHODS and not _is_json(
            headers.get(b"content-type", [])
        ):
            return UnsupportedMediaTypeError()
        return None

    def _authorized(self, headers: dict[bytes, list[bytes]]) -> bool:
        """The key in `Authorization: Bearer` or in `x-api-key`, compared in constant time.

        A repeated key header is refused: two readers could pick different copies. If both
        headers are sent, both must hold the key: a conflict is refused, never resolved.
        """
        bearer = headers.get(b"authorization", [])
        api_key = headers.get(b"x-api-key", [])
        if len(bearer) > 1 or len(api_key) > 1 or not (bearer or api_key):
            return False
        matches = [
            hmac.compare_digest(bearer[0] if bearer else b"", self._bearer),
            hmac.compare_digest(api_key[0] if api_key else b"", self._key),
        ]
        sent = [bool(bearer), bool(api_key)]
        return all(match for match, present in zip(matches, sent, strict=True) if present)
