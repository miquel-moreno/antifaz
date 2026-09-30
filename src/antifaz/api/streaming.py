"""Streamed answers (ADR-0013, part 5c): relay the provider's server-sent events, restored.

The request was masked and checked by the guard like any other; only the answer streams.
Bytes are decoded as UTF-8 incrementally (a character may be cut between chunks), parsed as
SSE, checked for keys (invariant 13) and handed to the provider's transformer.

If the stream fails midway (timeout, lost connection, malformed SSE, a size limit, a key in
the answer, or a stream that ends before its last event), the text already safe is sent, then
ONE error event in the provider's format with a fixed message, and the stream is closed. The
provider's stream is always closed, also when the client goes away.

Forbidden: logging bodies, events or keys. Putting a received value in an error message.
"""

import codecs
import inspect
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import anyio
import httpx
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from antifaz.api.proxy import json_texts
from antifaz.providers.sse import MalformedStream, SSEEvent, SSEParser
from antifaz.providers.streaming import StreamCut, StreamTransformer
from antifaz.restore import StreamLimitExceeded

logger = logging.getLogger("antifaz.stream")

# Fields whose text flows across events: a key could be split between two of them.
_FLOWING_TEXT_KEYS = frozenset(
    {"content", "refusal", "arguments", "text", "partial_json", "thinking"}
)


@dataclass
class StreamCounters:
    """In-memory counters of the streamed answers (no values, only how many)."""

    unknown_events: int = 0  # unknown events and fields that passed untouched
    failed_streams: int = 0  # streams stopped with an error event


@dataclass(frozen=True)
class Failure:
    code: str
    message: str


TIMEOUT = Failure("upstream_timeout", "the provider stopped answering in time")
CONNECTION = Failure("upstream_unavailable", "the connection to the provider was lost")
MALFORMED = Failure("bad_upstream_response", "the provider sent a malformed stream")
LIMIT = Failure("stream_limit_exceeded", "the provider's answer passed a size limit")
KEY = Failure("bad_upstream_response", "the provider's answer contained a key and was dropped")
CUT = Failure("upstream_stream_cut", "the provider's stream ended before it was complete")
INTERNAL = Failure("stream_failed", "the gateway could not relay the provider's stream")


class KeyEchoed(Exception):  # noqa: N818 - reads as the event it names
    def __init__(self) -> None:
        super().__init__("the provider's answer contained a key")  # fixed: never the key


def _flowing_texts(node: Any) -> Iterator[str]:
    """Text values under the keys where streamed text arrives, in order."""
    stack: list[tuple[str | None, Any]] = [(None, node)]
    while stack:
        name, current = stack.pop()
        if isinstance(current, str):
            if name in _FLOWING_TEXT_KEYS:
                yield current
        elif isinstance(current, dict):
            stack.extend(reversed(list(current.items())))
        elif isinstance(current, list):
            stack.extend((name, item) for item in reversed(current))


class KeyWatch:
    """Invariant 13 in a stream: no configured key may reach the client.

    Each event is checked raw and through its decoded JSON strings and keys (escapes such as
    "\\u0061" undone). The text fields are also checked joined with the end of the previous
    ones, so a key split between two deltas is caught when it completes (the part already sent
    is not a key).
    """

    def __init__(self, keys: Sequence[str]) -> None:
        self._keys = [key for key in keys if key]
        self._keep = max((len(key) for key in self._keys), default=1) - 1
        self._tail = ""

    def check(self, event: SSEEvent) -> None:
        texts = [event.raw]
        parsed: Any = None
        if event.data is not None:
            texts.extend(json_texts(event.data))
            try:
                parsed = json.loads(event.data)
            except (ValueError, RecursionError):
                parsed = None
        if any(key in text for text in texts for key in self._keys):
            raise KeyEchoed()
        flowing = self._tail + "".join(_flowing_texts(parsed))
        if any(key in flowing for key in self._keys):
            raise KeyEchoed()
        self._tail = flowing[-self._keep :] if self._keep > 0 else ""


async def _restored(
    upstream: httpx.Response, transformer: StreamTransformer, watch: KeyWatch
) -> AsyncIterator[str]:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    parser = SSEParser()
    async for chunk in upstream.aiter_bytes():
        for event in parser.feed(decoder.decode(chunk)):
            watch.check(event)
            out = transformer.event(event)
            if out:
                yield out
    parser.feed(decoder.decode(b"", final=True))
    if not parser.finished:
        raise StreamCut()  # the last event never ended
    out = transformer.end()
    if out:
        yield out


def _failure(error: Exception) -> Failure:
    if isinstance(error, httpx.TimeoutException):
        return TIMEOUT
    if isinstance(error, httpx.HTTPError):
        return CONNECTION
    if isinstance(error, UnicodeDecodeError | MalformedStream):
        return MALFORMED
    if isinstance(error, StreamLimitExceeded):
        return LIMIT
    if isinstance(error, KeyEchoed):
        return KEY
    if isinstance(error, StreamCut):
        return CUT
    # Only the class name: an exception message could quote the provider's text.
    logger.error("unexpected error while relaying a stream: %s", type(error).__name__)
    return INTERNAL


async def relay(
    upstream: httpx.Response,
    transformer: StreamTransformer,
    keys: Sequence[str],
    counters: StreamCounters,
) -> AsyncGenerator[bytes, None]:
    """The restored stream for the client. The provider's stream is closed in every case."""
    failure: Failure | None = None
    try:
        async for piece in _restored(upstream, transformer, KeyWatch(keys)):
            yield piece.encode("utf-8")
    except Exception as error:  # every failure ends in a fixed error event, never a traceback
        failure = _failure(error)
    finally:
        counters.unknown_events += transformer.unknown
        with anyio.CancelScope(shield=True):  # also when the client went away (cancelled)
            await upstream.aclose()
    if failure is not None:
        counters.failed_streams += 1
        logger.warning("stream stopped: %s", failure.code)
        yield transformer.abort(failure.code, failure.message).encode("utf-8")


class RelayResponse(StreamingResponse):
    """A StreamingResponse that always closes the relay and the provider's stream.

    If the client goes away, the server cancels the response while the relay may be waiting
    on the client (so its own `finally` never runs). Closing both here, shielded from that
    cancellation, leaves no provider connection open.
    """

    def __init__(self, relay: AsyncGenerator[bytes, None], upstream: httpx.Response) -> None:
        super().__init__(
            relay,
            status_code=upstream.status_code,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
        self._relay = relay
        self._upstream = upstream

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                if inspect.getasyncgenstate(self._relay) != inspect.AGEN_RUNNING:
                    await self._relay.aclose()
                await self._upstream.aclose()  # also if the relay never started


def stream_response(
    upstream: httpx.Response,
    transformer: StreamTransformer,
    keys: Sequence[str],
    counters: StreamCounters,
) -> StreamingResponse:
    return RelayResponse(relay(upstream, transformer, keys, counters), upstream)


__all__ = ["KeyWatch", "StreamCounters", "relay", "stream_response"]
