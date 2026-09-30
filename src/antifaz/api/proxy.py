"""Steps shared by the proxy routes (ADR-0013): auth, body, one serialization + guard, send.

Forbidden: logging bodies, headers or keys. Forwarding the client's key or headers. Taking the
destination from the client.
"""

import hmac
import json
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from fastapi import Request, Response

from antifaz import guard
from antifaz.api.errors import (
    BadUpstreamResponseError,
    InvalidRequestError,
    PayloadTooLargeError,
    StreamingNotSupportedError,
    UnauthorizedError,
    UpstreamRedirectError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from antifaz.vault import Vault


def authorize(credentials: Sequence[tuple[str, str]]) -> None:
    """Accept if any (given, expected) pair matches. Every pair is compared in constant time."""
    matches = [
        hmac.compare_digest(given.encode(), expected.encode()) for given, expected in credentials
    ]
    if not any(matches):
        raise UnauthorizedError()


async def read_json(request: Request, limit: int) -> dict[str, Any]:
    """The body as a JSON object, read with a size limit. Errors never quote the body."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise PayloadTooLargeError()
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise PayloadTooLargeError()
        chunks.append(chunk)
    return _parse(b"".join(chunks))


def _reject_constant(_: str) -> object:
    raise ValueError("NaN and Infinity are not valid JSON")


def _parse(raw: bytes) -> dict[str, Any]:
    parsed: object = None
    try:
        parsed = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        parsed = None
    # Raised outside the except block: the decode error (which may quote the body) is dropped.
    if not isinstance(parsed, dict):
        raise InvalidRequestError() from None
    return parsed


def refuse_streaming(body: dict[str, Any]) -> None:
    """`stream` may only be true, false or absent; true is refused until part 5c."""
    stream = body.get("stream", False)
    if not isinstance(stream, bool):
        raise InvalidRequestError()
    if stream:
        raise StreamingNotSupportedError()


async def send_masked(
    client: httpx.AsyncClient,
    url: str,
    headers: Mapping[str, str],
    masked: dict[str, Any],
    vault: Vault,
) -> httpx.Response:
    """Serialize once, run the guard on those exact bytes and send them. 3xx is never followed."""
    payload = json.dumps(masked, ensure_ascii=False, allow_nan=False).encode("utf-8")
    guard.check(payload, vault)  # the same bytes that are sent, right before sending
    try:
        upstream = await client.post(
            url, content=payload, headers={**headers, "Content-Type": "application/json"}
        )
    except httpx.TimeoutException:
        timed_out = True
    except httpx.HTTPError:
        timed_out = False
    else:
        if 300 <= upstream.status_code < 400:
            raise UpstreamRedirectError()  # the destination is fixed in settings
        return upstream
    # Outside the except block: the httpx error (with the URL and maybe more) is not chained.
    raise (UpstreamTimeoutError() if timed_out else UpstreamUnavailableError()) from None


def passthrough(upstream: httpx.Response) -> Response:
    """The provider's answer as it is. It only saw masked text: nothing here is restored."""
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


def answer_json(upstream: httpx.Response) -> dict[str, Any]:
    """The provider's successful answer as a JSON object, or a fixed 502."""
    answer: object = None
    try:
        answer = upstream.json()
    except (UnicodeDecodeError, ValueError):
        answer = None
    if not isinstance(answer, dict):
        raise BadUpstreamResponseError() from None
    return answer
