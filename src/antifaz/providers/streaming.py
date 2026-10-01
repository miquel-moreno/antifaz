"""Shared pieces of the streamed answers (ADR-0013, part 5c).

A transformer takes the provider's events one by one and returns the text to send to the
client: known text fields restored on the fly (StreamRestorer), tool arguments accumulated and
emitted restored once complete, everything else untouched. Unknown events and fields pass as
they came and are counted.

Forbidden: Never restore unknown events or fields. Never emit half accumulated tool arguments.
"""

import json
from collections.abc import Hashable
from typing import Any, Protocol, TypeGuard

from antifaz.providers.json_walk import too_deep
from antifaz.providers.sse import SSEEvent
from antifaz.restore import StreamLimitExceeded, StreamRestorer
from antifaz.vault import Vault

# Most characters of tool arguments accumulated in one streamed answer (all calls together).
MAX_ACCUMULATED_CHARS = 4 * 1024 * 1024


class StreamCut(Exception):  # noqa: N818 - reads as the event it names
    """The provider's stream ended without its final event. The message is fixed."""

    def __init__(self) -> None:
        super().__init__("the provider's stream ended before it was complete")


class StreamTransformer(Protocol):
    """What the relay needs from each provider format."""

    unknown: int  # unknown events and fields seen (they passed untouched)

    def event(self, event: SSEEvent) -> str:
        """The text to send for this event (maybe "", maybe more than one event)."""

    def end(self) -> str:
        """The provider closed the stream: what is left, or StreamCut if it was not finished."""

    def abort(self, code: str, message: str, *, safe_text: bool = True) -> str:
        """The safe text still held (unless `safe_text` is False), then an error event with a
        fixed code and message."""


def json_object(data: str | None) -> dict[str, Any] | None:
    """`data` as a JSON object, or None (not JSON, not an object, or nested too deep)."""
    if data is None:
        return None
    try:
        parsed = json.loads(data)
    except (ValueError, RecursionError):
        return None
    if not isinstance(parsed, dict) or too_deep(parsed):
        return None
    return parsed


def dumps(node: object) -> str:
    return json.dumps(node, ensure_ascii=False)


def is_index(value: object) -> TypeGuard[int]:
    """A plain non-negative int (not a bool, a float like 0.0 or a string like "0")."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class TextFields[K: Hashable]:
    """One StreamRestorer per streamed text field (a choice's content, a content block...)."""

    def __init__(self, vault: Vault) -> None:
        self._vault = vault
        self._restorers: dict[K, StreamRestorer] = {}

    def feed(self, key: K, text: str) -> str:
        restorer = self._restorers.get(key)
        if restorer is None:
            restorer = self._restorers[key] = StreamRestorer(self._vault)
        return restorer.feed(text)

    def flush(self, key: K) -> str:
        restorer = self._restorers.pop(key, None)
        return restorer.flush() if restorer is not None else ""

    def open_keys(self) -> list[K]:
        return list(self._restorers)


class Accumulator[K: Hashable]:
    """Pieces of tool arguments by key, with one size limit for the whole answer."""

    def __init__(self) -> None:
        self._parts: dict[K, list[str]] = {}
        self._size = 0

    def add(self, key: K, text: str) -> None:
        self._size += len(text)
        if self._size > MAX_ACCUMULATED_CHARS:
            raise StreamLimitExceeded()
        self._parts.setdefault(key, []).append(text)

    def pop(self, key: K) -> str | None:
        parts = self._parts.pop(key, None)
        return "".join(parts) if parts is not None else None

    def open_keys(self) -> list[K]:
        return list(self._parts)

    def clear(self) -> None:
        self._parts.clear()


__all__ = [
    "MAX_ACCUMULATED_CHARS",
    "Accumulator",
    "StreamCut",
    "StreamTransformer",
    "TextFields",
    "dumps",
    "is_index",
    "json_object",
]
