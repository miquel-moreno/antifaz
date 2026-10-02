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
from collections.abc import AsyncGenerator, Iterator, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

import anyio
import httpx
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from antifaz.api.proxy import key_texts
from antifaz.providers.sse import MalformedStream, SSEEvent, SSEParser
from antifaz.providers.streaming import StreamCut, StreamTransformer, is_index
from antifaz.restore import StreamLimitExceeded

logger = logging.getLogger("antifaz.stream")

# Most field paths (and field names) whose tail is kept in one stream; each tail is shorter
# than the longest key. A stream that needs more ends with an error: never forget a tail.
_MAX_TAILS = 4096


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


Path = tuple[object, ...]


def _leaves(node: Any) -> Iterator[tuple[Path, str]]:
    """Every string value of an event with its path. An object with an integer "index" (an
    OpenAI choice or tool call, an Anthropic content block) is named by that index, not by
    its position: the same field of the same choice or block has the same path in every
    event, whatever else comes in between."""
    stack: list[tuple[Path, Any]] = [((), node)]
    while stack:
        path, current = stack.pop()
        if isinstance(current, str):
            yield path, current
        elif isinstance(current, dict):
            index = current.get("index")
            if is_index(index):
                path = (*path, ("index", index))
            stack.extend(((*path, name), value) for name, value in current.items())
        elif isinstance(current, list):
            for position, item in enumerate(current):
                named = isinstance(item, dict) and is_index(item.get("index"))
                stack.append((path if named else (*path, position), item))


class KeyWatch:
    """Invariant 13 in a stream: no configured key may reach the client.

    Checked on what the gateway SENDS (after restoring: a value decoded from escapes in tool
    arguments could be a key), and on what the provider sent:
      - each event raw and every decoded layer of its data (JSON inside strings, escapes);
      - every string field joined with the end of the same field in earlier events (one tail
        per path: choice or block index and field name, known fields or not), so a key split
        between two deltas is caught when it completes. The part already sent is not a key.
      - and, as a second net, joined with the end of the last field of the same NAME in any
        path ("content", "text", "arguments"...), so an odd path cannot hide a split key.
    A stream with more than _MAX_TAILS paths or names ends (StreamLimitExceeded): forgetting
    an old tail would let a key split around it through.
    """

    def __init__(self, keys: Sequence[str]) -> None:
        self._keys = [key for key in keys if key]
        self._keep = max((len(key) for key in self._keys), default=1) - 1
        self._tails: dict[Path, str] = {}
        self._names: dict[str, str] = {}  # field name -> tail of its last value, any path

    def _holds(self, text: str) -> bool:
        return any(key in text for key in self._keys)

    def check(self, event: SSEEvent) -> None:
        """An event as the provider sent it: raw and decoded, no tails."""
        texts = [event.raw]
        if event.data is not None:
            texts.extend(key_texts(event.data))
        if any(self._holds(text) for text in texts):
            raise KeyEchoed()

    def check_output(self, text: str) -> None:
        """Text the gateway is about to send (whole events)."""
        if self._holds(text):
            raise KeyEchoed()
        for event in SSEParser().feed(text):
            self.check(event)
            try:
                parsed = json.loads(event.data) if event.data is not None else None
            except (ValueError, RecursionError):
                continue
            for path, value in _leaves(parsed):
                name = _name(path)
                joined = self._tails.get(path, "") + value
                by_name = self._names.get(name, "") + value
                if self._holds(joined) or self._holds(by_name):
                    raise KeyEchoed()
                if self._keep > 0:
                    _remember(self._tails, path, joined[-self._keep :])
                    _remember(self._names, name, by_name[-self._keep :])


def _name(path: Path) -> str:
    """The field name of a leaf: the last name in its path ("" for a bare string)."""
    return next((part for part in reversed(path) if isinstance(part, str)), "")


def _remember[K](tails: dict[K, str], key: K, tail: str) -> None:
    if key not in tails and len(tails) >= _MAX_TAILS:
        raise StreamLimitExceeded()  # fail closed: an old tail is never forgotten
    tails[key] = tail


async def _restored(
    upstream: httpx.Response, transformer: StreamTransformer, watch: KeyWatch
) -> AsyncGenerator[str, None]:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    parser = SSEParser()
    async for chunk in upstream.aiter_bytes():
        for event in parser.feed(decoder.decode(chunk)):
            watch.check(event)
            out = transformer.event(event)
            if out:
                watch.check_output(out)
                yield out
    parser.feed(decoder.decode(b"", final=True))
    if not parser.finished:
        raise StreamCut()  # the last event never ended
    out = transformer.end()
    if out:
        watch.check_output(out)
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
    watch = KeyWatch(keys)
    try:
        async with aclosing(_restored(upstream, transformer, watch)) as pieces:
            async for piece in pieces:
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
        last = transformer.abort(failure.code, failure.message)
        try:
            watch.check_output(last)
        except KeyEchoed:  # the held text completes a key: only the error goes out
            last = transformer.abort(KEY.code, KEY.message, safe_text=False)
        except StreamLimitExceeded:  # too many fields to check the held text: only the error
            last = transformer.abort(failure.code, failure.message, safe_text=False)
        yield last.encode("utf-8")


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
