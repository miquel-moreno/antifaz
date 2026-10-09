"""Steps shared by the proxy routes (ADR-0013): body, one serialization + guard, send.

The key, Origin and Content-Type are checked before any route, in `api/gate.py` (ADR-0015).

Forbidden: logging bodies, headers or keys. Forwarding the client's key or headers. Taking the
destination from the client.
"""

import json
from collections.abc import Iterator, Mapping, Sequence
from itertools import permutations
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
from antifaz.providers.json_walk import READ_KEYS, fold_key, too_deep
from antifaz.vault import Vault


def configured_keys(settings: Settings) -> list[str]:
    """Every key in the settings: none of them may ever reach the client (invariant 13).

    The panel's admin token too (ADR-0018): no client value may carry it to a provider and no
    provider answer may carry it back."""
    secrets = (
        settings.antifaz_api_key,
        settings.openai_api_key,
        settings.anthropic_api_key,
        settings.admin_token,
    )
    return [secret.get_secret_value() for secret in secrets if secret is not None]


# Client values joined in every order (a key split across them) up to this many values.
_MAX_JOINED = 4


def client_values_hold_a_key(values: Sequence[str], keys: Sequence[str]) -> bool:
    """True if a key is in one of `values` or in any concatenation of them, in any order.

    For the few short values a client may forward (query parameters, anthropic-* headers): a
    key split across two of them would reach the provider whole. More values than _MAX_JOINED
    count as holding a key (fails closed; no route forwards that many).
    """
    if len(values) > _MAX_JOINED:
        return True
    orders = (order for n in range(1, len(values) + 1) for order in permutations(values, n))
    joined = ("".join(order) for order in orders)
    return any(key in text for text in joined for key in keys)


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
    """An object without repeated keys (ADR-0015).

    With {"content": A, "content": B} one reader checks A and another sends B: always refused.
    Keys that only differ in case or width ("content" and "Content") are refused when they fold
    to a key the gateway reads (READ_KEYS); a schema may have "name" and "Name".
    """
    exact: set[str] = set()
    folded_read: set[str] = set()
    for key, _ in pairs:
        folded = fold_key(key)
        if key in exact or (folded in READ_KEYS and folded in folded_read):
            raise ValueError("repeated key")  # the key is never in the message
        exact.add(key)
        if folded in READ_KEYS:
            folded_read.add(folded)
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


def stream_requested(body: dict[str, Any]) -> bool:
    """`stream` may only be true, false or absent."""
    stream = body.get("stream", False)
    if not isinstance(stream, bool):
        raise InvalidRequestError()
    return stream


def refuse_streaming(body: dict[str, Any]) -> None:
    """For routes that never stream (count_tokens): `stream: true` is refused."""
    if stream_requested(body):
        raise StreamingNotSupportedError()


def is_event_stream(upstream: httpx.Response) -> bool:
    """True if the provider answered with server-sent events."""
    media: str = upstream.headers.get("content-type", "").split(";", 1)[0]
    return media.strip().lower() == "text/event-stream"


async def send_masked(
    client: httpx.AsyncClient,
    url: str,
    headers: Mapping[str, str],
    masked: dict[str, Any],
    vault: Vault,
    keys: Sequence[str],
    *,
    stream: bool = False,
) -> httpx.Response:
    """Serialize once, run the guard on those exact bytes and send them. 3xx is never followed.

    An answer that repeats one of `keys` (a careless provider echoing the headers it got) is
    dropped with a fixed 502: its body would hand the provider key to the client.

    With `stream`, a successful answer in server-sent events comes back OPEN (the caller
    relays and closes it; its events are checked for keys one by one). Any other answer
    (an error, or JSON when the provider did not stream) is read whole and checked here.
    """
    payload = json.dumps(masked, ensure_ascii=False, allow_nan=False).encode("utf-8")
    guard.check(payload, vault)  # the same bytes that are sent, right before sending
    request = client.build_request(
        "POST", url, content=payload, headers={**headers, "Content-Type": "application/json"}
    )
    return await _send(client, request, keys, stream=stream)


async def send_get(
    client: httpx.AsyncClient,
    url: str,
    headers: Mapping[str, str],
    params: Sequence[tuple[str, str]],
    keys: Sequence[str],
) -> httpx.Response:
    """A GET without a body (the model list): the same fixed errors, no redirects and the
    same check for keys in the answer as send_masked. `params` must be checked already."""
    request = client.build_request("GET", url, params=list(params), headers=dict(headers))
    return await _send(client, request, keys, stream=False)


async def _send(
    client: httpx.AsyncClient, request: httpx.Request, keys: Sequence[str], *, stream: bool
) -> httpx.Response:
    try:
        upstream = await client.send(request, stream=stream)
    except httpx.TimeoutException:
        timed_out = True
    except httpx.HTTPError:
        timed_out = False
    else:
        return await _checked(upstream, keys, stream=stream)
    # Outside the except block: the httpx error (with the URL and maybe more) is not chained.
    raise (UpstreamTimeoutError() if timed_out else UpstreamUnavailableError()) from None


async def _checked(
    upstream: httpx.Response, keys: Sequence[str], *, stream: bool
) -> httpx.Response:
    if 300 <= upstream.status_code < 400:
        await upstream.aclose()
        raise UpstreamRedirectError()  # the destination is fixed in settings
    if stream and upstream.is_success and is_event_stream(upstream):
        if _holds_a_key(_content_type(upstream), keys):
            await upstream.aclose()
            raise UpstreamEchoedKeyError()
        return upstream  # open: its events are checked as they arrive
    if stream:
        await _read_all(upstream)
    if _echoes_a_key(upstream, keys):
        raise UpstreamEchoedKeyError()
    return upstream


async def _read_all(upstream: httpx.Response) -> None:
    """Read a streamed answer whole (an error, or JSON), with the same fixed errors."""
    try:
        await upstream.aread()
    except httpx.TimeoutException:
        timed_out = True
    except httpx.HTTPError:
        timed_out = False
    else:
        return
    finally:
        await upstream.aclose()
    raise (UpstreamTimeoutError() if timed_out else UpstreamUnavailableError()) from None


def json_texts(raw: bytes | str) -> Iterator[str]:
    """Every decoded string and key of `raw` if it is JSON ("\\u0061" and "\\/" undone)."""
    try:
        stack: list[Any] = [json.loads(raw)]
    except (UnicodeDecodeError, ValueError, RecursionError):
        return
    while stack:  # iterative: a deep answer cannot raise RecursionError here
        node = stack.pop()
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            yield from node.keys()
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def _content_type(upstream: httpx.Response) -> bytes:
    value: str = upstream.headers.get("content-type", "")
    return value.encode("latin-1", "replace")


def _holds_a_key(raw: bytes, keys: Sequence[str]) -> bool:
    return any(key.encode() in raw for key in keys)


def _decoded(text: str) -> list[str]:
    """One layer down: the strings of `text` if it is JSON, and `text` with its escapes undone."""
    out: list[str] = []
    if text.lstrip()[:1] in ("{", "[", '"'):
        out.extend(json_texts(text))
    if _BACKSLASH in text:
        try:
            unescaped = json.loads('"' + text + '"')  # a JSON string body with its escapes
        except (ValueError, RecursionError):
            unescaped = None
        if isinstance(unescaped, str) and unescaped != text:
            out.append(unescaped)
    return out


_BACKSLASH = chr(92)
# How many times a string is decoded again: JSON in a string (tool arguments) holding escapes.
_LAYERS = 3


def key_texts(raw: bytes | str) -> Iterator[str]:
    """`raw` and every string inside it, decoded again where a string holds JSON or escapes.

    Up to three layers, so a key written as escapes inside tool arguments (JSON inside a JSON
    string: "\\\\u0061" on the wire) is seen in clear.
    """
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    pending = [(text, _LAYERS)]
    while pending:
        current, layers = pending.pop()
        yield current
        if layers:
            pending.extend((nested, layers - 1) for nested in _decoded(current))


def contains_key(raw: bytes | str, keys: Sequence[str]) -> bool:
    """True if a key is in `raw` as it is or in any decoded layer of it (invariant 13)."""
    return any(key in text for text in key_texts(raw) for key in keys)


def _echoes_a_key(upstream: httpx.Response, keys: Sequence[str]) -> bool:
    """True if the body (raw or any decoded layer) or the content type holds a key."""
    if _holds_a_key(_content_type(upstream), keys):
        return True
    return contains_key(upstream.content, keys)


def restored_json(body: dict[str, Any], keys: Sequence[str], status_code: int) -> Response:
    """The restored answer, checked for keys AFTER restoring (a value decoded from escapes in
    tool arguments could be one): a fixed 502 if it holds one."""
    content = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if contains_key(content, keys):
        raise UpstreamEchoedKeyError()
    return Response(content=content, status_code=status_code, media_type="application/json")


def passthrough(upstream: httpx.Response) -> Response:
    """The provider's answer as it is (it only saw masked text: nothing here is restored),
    always as application/json. A body that is not JSON (an HTML error page...) is a fixed
    502: whatever the provider's content type, the client only ever gets JSON."""
    try:
        json.loads(upstream.content)
    except (UnicodeDecodeError, ValueError, RecursionError):
        is_json = False
    else:
        is_json = True
    if not is_json:
        raise BadUpstreamResponseError() from None
    return Response(
        content=upstream.content, status_code=upstream.status_code, media_type="application/json"
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
