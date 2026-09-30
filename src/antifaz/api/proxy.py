"""Steps shared by the proxy routes (ADR-0013): body, one serialization + guard, send.

The key, Origin and Content-Type are checked before any route, in `api/gate.py` (ADR-0015).

Forbidden: logging bodies, headers or keys. Forwarding the client's key or headers. Taking the
destination from the client.
"""

import json
import unicodedata
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
    UpstreamEchoedKeyError,
    UpstreamRedirectError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from antifaz.config import Settings
from antifaz.providers.json_walk import too_deep
from antifaz.vault import Vault


def configured_keys(settings: Settings) -> list[str]:
    """Every key in the settings: none of them may ever reach the client (invariant 13)."""
    secrets = (settings.antifaz_api_key, settings.openai_api_key, settings.anthropic_api_key)
    return [secret.get_secret_value() for secret in secrets if secret is not None]


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


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """An object whose keys are all different, also ignoring case and width (ADR-0015).

    With {"content": A, "content": B} one reader checks A and another sends B; with "content"
    and "Content" a case-insensitive reader sees two copies of one field. Both are refused.
    """
    seen: set[str] = set()
    for key, _ in pairs:
        folded = unicodedata.normalize("NFKC", key).casefold()
        if folded in seen:
            raise ValueError("repeated key")  # the key is never in the message
        seen.add(folded)
    return dict(pairs)


def _encodable(node: object) -> bool:
    try:
        json.dumps(node, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _parse(raw: bytes) -> dict[str, Any]:
    parsed: object = None
    try:
        parsed = json.loads(
            raw.decode("utf-8"), parse_constant=_reject_constant, object_pairs_hook=_unique_keys
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        parsed = None
    if isinstance(parsed, dict) and not _encodable(parsed):
        parsed = None  # a lone surrogate ("D800") cannot be sent as UTF-8
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
    keys: Sequence[str],
) -> httpx.Response:
    """Serialize once, run the guard on those exact bytes and send them. 3xx is never followed.

    An answer that repeats one of `keys` (a careless provider echoing the headers it got) is
    dropped with a fixed 502: its body would hand the provider key to the client.
    """
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
        if _echoes_a_key(upstream, keys):
            raise UpstreamEchoedKeyError()
        return upstream
    # Outside the except block: the httpx error (with the URL and maybe more) is not chained.
    raise (UpstreamTimeoutError() if timed_out else UpstreamUnavailableError()) from None


def _echoes_a_key(upstream: httpx.Response, keys: Sequence[str]) -> bool:
    """True if the body or the content type (the only header returned) holds a key."""
    content_type = upstream.headers.get("content-type", "").encode("latin-1", "replace")
    return any(key.encode() in upstream.content or key.encode() in content_type for key in keys)


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
    except (UnicodeDecodeError, ValueError, RecursionError):
        answer = None
    # Too deep to restore without recursion: a fixed 502, never a 500.
    if not isinstance(answer, dict) or too_deep(answer):
        raise BadUpstreamResponseError() from None
    return answer
