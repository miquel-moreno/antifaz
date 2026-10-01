"""Shared pieces of the proxy tests: a fake upstream (httpx.MockTransport) and test keys.

The fake upstream can also answer with server-sent events (streaming, part 5c): a byte stream
cut in chunks of any size, slow, failing or hanging, that records whether it was closed.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import httpx

from antifaz import Span
from antifaz.detect.scan import scan
from tests.conftest import SENTINEL_DNI

# Obviously fake keys, only for tests. The gateway key must be 32+ characters to start.
GATEWAY_KEY = "test-gateway-key-not-real-0123456789abcdef"
PROVIDER_KEY = "test-provider-key-not-real"

Handler = Callable[[httpx.Request], httpx.Response]


class FakeUpstream:
    """Records every request and answers with the given handler."""

    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []
        self.handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def raiser(error: type[httpx.HTTPError]) -> Handler:
    """A handler that fails with `error`, whose message carries the sentinel."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise error(f"boom {SENTINEL_DNI}", request=request)  # type: ignore[call-arg]

    return handler


def models_list(request: httpx.Request) -> httpx.Response:
    """GET .../models: an Anthropic list if the request carries anthropic-version, else OpenAI."""
    if "anthropic-version" in request.headers:
        model = {
            "type": "model",
            "id": "claude-x",
            "display_name": "Claude X",
            "created_at": "2026-01-01T00:00:00Z",
        }
        body: dict[str, Any] = {
            "data": [model],
            "has_more": False,
            "first_id": "claude-x",
            "last_id": "claude-x",
        }
    else:
        body = {
            "object": "list",
            "data": [{"id": "gpt-x", "object": "model", "created": 0, "owned_by": "system"}],
        }
    return httpx.Response(200, json=body)


def misses_repeats(text: str) -> Sequence[Span]:
    """A detector that only reports the first appearance: the guard must catch the rest."""
    return scan(text)[:1]


def compact(text: str) -> str:
    return "".join(c for c in text.casefold() if c.isalnum())


# --- Streaming (server-sent events) ----------------------------------------------------------


class ChunkStream(httpx.AsyncByteStream):
    """The body of a streamed answer: `chunks` in order, then an error or a hang if asked."""

    def __init__(
        self,
        chunks: Sequence[bytes],
        *,
        delay: float = 0.0,
        error: Exception | None = None,
        hang: bool = False,
    ) -> None:
        self.chunks = list(chunks)
        self.delay = delay
        self.error = error
        self.hang = hang
        self.sent = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.sent += 1
            yield chunk
        if self.error is not None:
            raise self.error
        if self.hang:
            await asyncio.Event().wait()  # never set: only a disconnect ends it

    async def aclose(self) -> None:
        self.closed = True


def split_bytes(data: bytes, size: int | None) -> list[bytes]:
    if not size:
        return [data]
    return [data[i : i + size] for i in range(0, len(data), size)]


def sse_response(
    text: str | bytes,
    *,
    split: int | None = None,
    status: int = 200,
    content_type: str = "text/event-stream",
    **options: Any,
) -> tuple[httpx.Response, ChunkStream]:
    """A streamed answer with `text` cut in chunks of `split` bytes (UTF-8 may be cut too)."""
    data = text.encode("utf-8") if isinstance(text, str) else text
    stream = ChunkStream(split_bytes(data, split), **options)
    response = httpx.Response(status, headers={"content-type": content_type}, stream=stream)
    return response, stream


def _data(payload: object) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def openai_sse(
    pieces: Sequence[str], *, finish: str = "stop", done: bool = True, choice: int = 0
) -> str:
    """An OpenAI Chat stream whose content arrives in `pieces`."""
    head = {"id": "chatcmpl-1", "object": "chat.completion.chunk", "model": "gpt-x"}
    events = [_data({**head, "choices": [{"index": choice, "delta": {"role": "assistant"}}]})]
    events += [
        _data({**head, "choices": [{"index": choice, "delta": {"content": piece}}]})
        for piece in pieces
    ]
    events.append(
        _data({**head, "choices": [{"index": choice, "delta": {}, "finish_reason": finish}]})
    )
    if done:
        events.append("data: [DONE]\n\n")
    return "".join(events)


TEXT_BLOCK_START = {
    "type": "content_block_start",
    "index": 0,
    "content_block": {"type": "text", "text": ""},
}


def anthropic_event(payload: dict[str, Any]) -> str:
    return f"event: {payload['type']}\n{_data(payload)}"


def anthropic_sse(pieces: Sequence[str], *, done: bool = True) -> str:
    """An Anthropic Messages stream with one text block whose text arrives in `pieces`."""
    message = {"id": "msg_1", "type": "message", "role": "assistant", "content": []}
    events = [
        anthropic_event({"type": "message_start", "message": message}),
        anthropic_event(TEXT_BLOCK_START),
        anthropic_event({"type": "ping"}),
    ]
    events += [
        anthropic_event(
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": p}}
        )
        for p in pieces
    ]
    events.append(anthropic_event({"type": "content_block_stop", "index": 0}))
    events.append(
        anthropic_event(
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"x": 1}}
        )
    )
    if done:
        events.append(anthropic_event({"type": "message_stop"}))
    return "".join(events)


def pieces_of(text: str, size: int = 3) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def sse_payloads(text: str) -> list[Any]:
    """The JSON of every data field of an SSE text ("[DONE]" kept as a string)."""
    out: list[Any] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        data = [line[6:] if line.startswith("data: ") else line[5:] for line in block.split("\n")
                if line.startswith("data:")]  # fmt: skip
        if data:
            joined = "\n".join(data)
            out.append(joined if joined == "[DONE]" else json.loads(joined))
    return out


def openai_text(text: str, field: str = "content") -> str:
    """The text a client rebuilds from the OpenAI stream `text` (all choices, in order)."""
    parts: list[str] = []
    for payload in sse_payloads(text):
        if isinstance(payload, dict):
            for choice in payload.get("choices", []):
                value = choice.get("delta", {}).get(field)
                if isinstance(value, str):
                    parts.append(value)
    return "".join(parts)


def anthropic_text(text: str) -> str:
    """The text a client rebuilds from the text_delta events of an Anthropic stream."""
    parts: list[str] = []
    for payload in sse_payloads(text):
        delta = payload.get("delta", {}) if isinstance(payload, dict) else {}
        if payload.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
            parts.append(delta["text"])
    return "".join(parts)
