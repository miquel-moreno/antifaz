"""Server-sent events (the streaming format of both providers): parse and serialize.

Small and dependency free (ADR-0013). Follows the WHATWG rules: lines end in CRLF, LF or CR;
"name: value" (one space after the colon is dropped); a line starting with ":" is a comment;
a blank line ends the event. Every field is kept in order, unknown ones too, and so is the
exact raw text, so an event the gateway does not change goes out byte for byte as it came.

Lines and events have a size limit: a longer one is a malformed stream (fixed message).

Forbidden: Never put a received value in an error message.
"""

import re
from dataclasses import dataclass

# Longest line and longest event accepted from the provider (characters).
MAX_LINE_CHARS = 1024 * 1024
MAX_EVENT_CHARS = 4 * 1024 * 1024

_LINE_END = re.compile(r"\r\n|\r|\n")
_CR = chr(13)
_LF = chr(10)


class MalformedStream(Exception):  # noqa: N818 - reads as the event it names
    """The provider's stream is not valid SSE (or too big to be). The message is fixed."""

    def __init__(self) -> None:
        super().__init__("the provider's stream is malformed")


@dataclass(frozen=True, slots=True)
class SSEEvent:
    """One event: its fields in order ("" is a comment) and the exact text it came as."""

    fields: tuple[tuple[str, str], ...]
    raw: str

    @property
    def event(self) -> str | None:
        """The last `event:` value, or None."""
        names = [value for name, value in self.fields if name == "event"]
        return names[-1] if names else None

    @property
    def data(self) -> str | None:
        """Every `data:` value joined with "\\n", or None if there is no data field."""
        values = [value for name, value in self.fields if name == "data"]
        return "\n".join(values) if values else None


def _line(name: str, value: str) -> str:
    return f":{value}\n" if name == "" else f"{name}: {value}\n"


def format_event(event: str | None, data: str) -> str:
    """A new event with an optional `event:` line and `data` (one line per "\\n")."""
    head = _line("event", event) if event is not None else ""
    return head + "".join(_line("data", part) for part in data.split("\n")) + "\n"


def replace_data(event: SSEEvent, data: str) -> str:
    """`event` serialized again with `data` in place of its data lines; other fields kept."""
    lines: list[str] = []
    placed = False
    for name, value in event.fields:
        if name != "data":
            lines.append(_line(name, value))
        elif not placed:
            lines.extend(_line("data", part) for part in data.split("\n"))
            placed = True
    if not placed:
        lines.extend(_line("data", part) for part in data.split("\n"))
    return "".join(lines) + "\n"


class SSEParser:
    """Incremental parser: feed() decoded text as it arrives, get the finished events."""

    def __init__(self) -> None:
        self._partial: list[str] = []  # the line being received, in pieces
        self._partial_size = 0
        self._fields: list[tuple[str, str]] = []
        self._raw: list[str] = []
        self._size = 0
        self._skip_lf = False  # the last chunk ended in CR: a LF right after belongs to it

    @property
    def finished(self) -> bool:
        """True when no line or event is half received."""
        return not self._partial and not self._raw

    def feed(self, text: str) -> list[SSEEvent]:
        if not text:
            return []
        if self._skip_lf:
            self._skip_lf = False
            if text[0] == _LF:  # the end of a CRLF split between two chunks
                if self._raw:
                    self._raw.append(_LF)
                text = text[1:]
        events: list[SSEEvent] = []
        start = 0
        for match in _LINE_END.finditer(text):
            piece = text[start : match.start()]
            line = "".join([*self._partial, piece]) if self._partial else piece
            self._partial, self._partial_size = [], 0
            event = self._take_line(line, match.group())
            if event is not None:
                events.append(event)
            start = match.end()
        if start < len(text):
            self._partial.append(text[start:])
            self._partial_size += len(text) - start
            if self._partial_size > MAX_LINE_CHARS:
                raise MalformedStream()
        else:
            self._skip_lf = text.endswith(_CR)
        return events

    def _take_line(self, line: str, end: str) -> SSEEvent | None:
        if len(line) > MAX_LINE_CHARS:
            raise MalformedStream()
        self._raw.append(line + end)
        self._size += len(line) + len(end)
        if self._size > MAX_EVENT_CHARS:
            raise MalformedStream()
        if line:
            name, colon, value = line.partition(":")
            if name and colon and value.startswith(" "):  # a comment keeps its text as it is
                value = value[1:]
            self._fields.append((name, value))
            return None
        event = SSEEvent(tuple(self._fields), "".join(self._raw)) if self._fields else None
        self._fields, self._raw, self._size = [], [], 0
        return event


__all__ = [
    "MAX_EVENT_CHARS",
    "MAX_LINE_CHARS",
    "MalformedStream",
    "SSEEvent",
    "SSEParser",
    "format_event",
    "replace_data",
]
