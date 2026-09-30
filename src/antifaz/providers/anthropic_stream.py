"""Anthropic Messages streaming (`stream: true`): restore the events on the fly (5c).

Events (ADR-0013):
  - `content_block_delta` with `text_delta`: restored per block index (StreamRestorer).
  - `content_block_delta` with `input_json_delta`: accumulated per block index and emitted
    restored as ONE `input_json_delta` right before `content_block_stop` (valid JSON,
    invariant 4). Tool input arrives at the end of its block, not piece by piece.
  - `thinking_delta` and `signature_delta` pass byte for byte (invariant 9), like every
    `thinking` and `redacted_thinking` block.
  - `message_stop` flushes what is left. `ping`, `error`, `message_start` and `message_delta`
    pass untouched; unknown events and delta types pass untouched and are counted.

Forbidden: Never change a reasoning block or its signature. Never restore unknown events.
"""

from typing import Any

from antifaz.providers.json_walk import restore_json_text, restore_strings
from antifaz.providers.sse import SSEEvent, format_event, replace_data
from antifaz.providers.streaming import (
    Accumulator,
    StreamCut,
    TextFields,
    dumps,
    is_index,
    json_object,
)
from antifaz.vault import Vault

PASS_EVENTS = frozenset({"ping", "error", "message_start", "message_delta"})
UNTOUCHED_DELTAS = frozenset({"thinking_delta", "signature_delta"})


def _delta_event(index: int, delta: dict[str, str]) -> str:
    payload = {"type": "content_block_delta", "index": index, "delta": delta}
    return format_event("content_block_delta", dumps(payload))


class AnthropicMessagesStream:
    """Transformer of one streamed Messages answer (see StreamTransformer)."""

    def __init__(self, vault: Vault) -> None:
        self._vault = vault
        self._text: TextFields[int] = TextFields(vault)
        self._json: Accumulator[int] = Accumulator()
        self._blocks: dict[int, object] = {}  # block index -> its type
        self._done = False
        self.unknown = 0

    def event(self, event: SSEEvent) -> str:
        if event.data is None:
            return event.raw  # a comment
        payload = json_object(event.data)
        kind = payload.get("type") if payload is not None else None
        if payload is None or kind in PASS_EVENTS:
            if payload is None:
                self.unknown += 1
            if kind == "error":
                self._done = True  # the provider ended the stream with its own error
            return event.raw
        if kind == "message_stop":
            self._done = True
            return self._flush_open(with_input=True) + event.raw
        index = payload.get("index")
        if kind in ("content_block_start", "content_block_delta", "content_block_stop") and (
            is_index(index)
        ):
            if kind == "content_block_start":
                return self._start(event, payload, index)
            if kind == "content_block_delta":
                return self._delta(event, payload, index)
            return self._close(index) + event.raw
        self.unknown += 1
        return event.raw

    def end(self) -> str:
        if not self._done:
            raise StreamCut()
        return ""

    def abort(self, code: str, message: str, *, safe_text: bool = True) -> str:
        self._json.clear()  # half tool input is not valid JSON: never emitted
        error = {"type": "error", "error": {"type": "api_error", "message": message}}
        text = self._flush_open(with_input=False) if safe_text else ""
        return text + format_event("error", dumps(error))

    def _start(self, event: SSEEvent, payload: dict[str, Any], index: int) -> str:
        block = payload.get("content_block")
        if not isinstance(block, dict):
            return event.raw
        kind = block.get("type")
        self._blocks[index] = kind
        if kind == "text" and isinstance(block.get("text"), str) and block["text"]:
            block["text"] = self._text.feed(index, block["text"])
        elif kind == "tool_use" and isinstance(block.get("input"), dict | list) and block["input"]:
            block["input"] = restore_strings(block["input"], self._vault)
        else:
            return event.raw  # thinking, redacted_thinking, empty blocks...: untouched
        return replace_data(event, dumps(payload))

    def _delta(self, event: SSEEvent, payload: dict[str, Any], index: int) -> str:
        delta = payload.get("delta")
        kind = delta.get("type") if isinstance(delta, dict) else None
        if isinstance(delta, dict) and kind == "text_delta" and isinstance(delta.get("text"), str):
            text = self._text.feed(index, delta["text"])
            if not text:
                return ""  # held back for now
            delta["text"] = text
            return replace_data(event, dumps(payload))
        if (
            isinstance(delta, dict)
            and kind == "input_json_delta"
            and isinstance(delta.get("partial_json"), str)
            and self._blocks.get(index) == "tool_use"  # only the input of blocks we restore
        ):
            self._json.add(index, delta["partial_json"])
            return ""
        if kind not in UNTOUCHED_DELTAS:
            self.unknown += 1
        return event.raw

    def _close(self, index: int, *, with_input: bool = True) -> str:
        """The held text and (if asked) the restored tool input of block `index`."""
        out = []
        rest = self._text.flush(index)
        if rest:
            out.append(_delta_event(index, {"type": "text_delta", "text": rest}))
        text = self._json.pop(index) if with_input else None
        if text:
            restored = restore_json_text(text, self._vault)
            out.append(_delta_event(index, {"type": "input_json_delta", "partial_json": restored}))
        return "".join(out)

    def _flush_open(self, *, with_input: bool) -> str:
        indexes = set(self._text.open_keys())
        if with_input:
            indexes |= set(self._json.open_keys())
        return "".join(self._close(index, with_input=with_input) for index in sorted(indexes))


__all__ = ["AnthropicMessagesStream"]
