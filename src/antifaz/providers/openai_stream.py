"""OpenAI Chat Completions streaming (`stream: true`): restore the chunks on the fly (5c).

Each chunk is `data: {...}` and the stream ends with `data: [DONE]` (ADR-0013):
  - `delta.content` and `delta.refusal` are restored per choice with a StreamRestorer each.
  - `delta.tool_calls[j].function.arguments` (and the legacy `function_call.arguments`) are
    accumulated per (choice, tool index) and emitted restored in ONE delta, in the chunk that
    finishes the choice (`finish_reason`): always valid JSON (invariant 4). The first delta
    of each call still carries its id, type and name, with empty arguments.
  - `[DONE]` flushes what is left. Anything else passes untouched; unknown events and delta
    fields are counted.
  - A choice without a plain integer `index` (or a tool call whose `index` is not one) is a
    malformed stream: a client would join it where the gateway cannot follow it (0.0, "0").

Forbidden: Never restore unknown fields. Never emit half accumulated arguments.
"""

from typing import Any

from antifaz.providers.json_walk import restore_json_text
from antifaz.providers.sse import MalformedStream, SSEEvent, format_event, replace_data
from antifaz.providers.streaming import (
    Accumulator,
    StreamCut,
    TextFields,
    dumps,
    is_index,
    json_object,
)
from antifaz.restore import StreamLimitExceeded
from antifaz.vault import Vault

TEXT_FIELDS = ("content", "refusal")
KNOWN_DELTA_KEYS = frozenset({"role", "content", "refusal", "tool_calls", "function_call"})
_LEGACY = -1  # tool index used for the legacy `function_call`
_DONE = "[DONE]"
# Top-level fields of a chunk that say nothing on their own: a chunk with only these and
# empty deltas can be skipped while its text is held back.
_ENVELOPE_KEYS = frozenset(
    {"id", "object", "created", "model", "system_fingerprint", "service_tier", "choices", "usage"}
)


def _empty(value: object) -> bool:
    return value in ("", None, [], {})


class OpenAIChatStream:
    """Transformer of one streamed Chat Completions answer (see StreamTransformer)."""

    def __init__(self, vault: Vault) -> None:
        self._vault = vault
        self._text: TextFields[tuple[int, str]] = TextFields(vault)
        self._arguments: Accumulator[tuple[int, int]] = Accumulator()
        self._envelope: dict[str, Any] = {"object": "chat.completion.chunk"}
        self._done = False
        self._failed: StreamLimitExceeded | None = None
        self._salvaged = ""  # what the chunk that hit a limit had already restored
        self.unknown = 0

    def event(self, event: SSEEvent) -> str:
        data = event.data
        if data is None:
            return event.raw  # a comment (keep-alive)
        if event.event not in (None, "message"):
            self.unknown += 1
            return event.raw
        if data.strip() == _DONE:
            self._done = True
            return self._flush_open(with_arguments=True) + event.raw
        chunk = json_object(data)
        if chunk is None or not isinstance(chunk.get("choices"), list):
            if chunk is not None and "error" in chunk:
                self._done = True  # the provider ended the stream with its own error
            else:
                self.unknown += 1
            return event.raw
        self._envelope = {k: v for k, v in chunk.items() if k not in ("choices", "usage")}
        changed = False
        for choice in chunk["choices"]:
            changed = self._choice(choice) or changed
        if self._failed is not None:
            # A choice hit a limit: the text the other choices restored is kept for abort().
            empty = self._only_empty_deltas(chunk)
            self._salvaged = "" if empty else replace_data(event, dumps(chunk))
            raise self._failed
        if not changed:
            return event.raw  # byte for byte
        if self._only_empty_deltas(chunk):
            return ""  # everything in it is held back for now
        return replace_data(event, dumps(chunk))

    def end(self) -> str:
        if not self._done:
            raise StreamCut()
        return ""

    def abort(self, code: str, message: str, *, safe_text: bool = True) -> str:
        self._arguments.clear()  # half arguments are not valid JSON: never emitted
        error = {"error": {"message": message, "type": "antifaz_error", "code": code}}
        text = self._salvaged + self._flush_open(with_arguments=False) if safe_text else ""
        self._salvaged = ""
        return text + format_event(None, dumps(error))

    # --- one choice -------------------------------------------------------------------------

    def _choice(self, choice: Any) -> bool:
        if not isinstance(choice, dict) or not is_index(choice.get("index")):
            raise MalformedStream()  # fail closed: it cannot be followed like the client does
        index: int = choice["index"]
        delta = choice.get("delta")
        changed = False
        if isinstance(delta, dict):
            self.unknown += sum(1 for key in delta if key not in KNOWN_DELTA_KEYS)
            for field in TEXT_FIELDS:
                if isinstance(delta.get(field), str):
                    delta[field] = self._text.feed((index, field), delta[field])
                    changed = True
            try:
                changed = self._take_arguments(index, delta) or changed
            except StreamLimitExceeded as error:
                self._failed = error  # raised by event() once every choice is done
                delta.pop("tool_calls", None)  # half arguments are never sent
                delta.pop("function_call", None)
                changed = True
        if choice.get("finish_reason") is not None:
            if not isinstance(delta, dict):
                delta = {}
            if self._finish(index, delta, with_arguments=self._failed is None):
                choice["delta"] = delta
                changed = True
        return changed

    def _take_arguments(self, index: int, delta: dict[str, Any]) -> bool:
        """Move the argument pieces of this delta into the accumulator."""
        changed = False
        calls = delta.get("tool_calls")
        if isinstance(calls, list):
            kept = []
            for call in calls:
                function = call.get("function") if isinstance(call, dict) else None
                if isinstance(call, dict) and "index" in call and not is_index(call["index"]):
                    raise MalformedStream()
                if not isinstance(call, dict) or not is_index(call.get("index")):
                    self.unknown += 1  # without an index it cannot be joined: passes as it is
                if (
                    isinstance(function, dict)
                    and is_index(call.get("index"))
                    and isinstance(function.get("arguments"), str)
                ):
                    self._arguments.add((index, call["index"]), function["arguments"])
                    function["arguments"] = ""
                    changed = True
                    if set(call) <= {"index", "function"} and set(function) == {"arguments"}:
                        continue  # only a piece of arguments: nothing left to send
                kept.append(call)
            if changed:
                if kept:
                    delta["tool_calls"] = kept
                else:
                    del delta["tool_calls"]
        legacy = delta.get("function_call")
        if isinstance(legacy, dict) and isinstance(legacy.get("arguments"), str):
            self._arguments.add((index, _LEGACY), legacy["arguments"])
            legacy["arguments"] = ""
            changed = True
            if set(legacy) == {"arguments"}:
                del delta["function_call"]
        return changed

    def _finish(self, index: int, delta: dict[str, Any], *, with_arguments: bool) -> bool:
        """Put in `delta` the held text and, if asked, the restored arguments of the choice."""
        changed = False
        for field in TEXT_FIELDS:
            rest = self._text.flush((index, field))
            if rest:
                before = delta.get(field)
                delta[field] = (before if isinstance(before, str) else "") + rest
                changed = True
        if not with_arguments:
            return changed
        for key in sorted(key for key in self._arguments.open_keys() if key[0] == index):
            restored = restore_json_text(self._arguments.pop(key) or "", self._vault)
            tool = key[1]
            if tool == _LEGACY:
                legacy = delta.get("function_call")
                if not isinstance(legacy, dict):
                    legacy = delta["function_call"] = {}
                legacy["arguments"] = restored
            else:
                self._put_arguments(delta, tool, restored)
            changed = True
        return changed

    @staticmethod
    def _put_arguments(delta: dict[str, Any], tool: int, arguments: str) -> None:
        calls = delta.get("tool_calls")
        if not isinstance(calls, list):
            calls = delta["tool_calls"] = []
        for call in calls:
            function = call.get("function") if isinstance(call, dict) else None
            if isinstance(function, dict) and call.get("index") == tool:
                function["arguments"] = arguments
                return
        calls.append({"index": tool, "function": {"arguments": arguments}})

    # --- whole stream -------------------------------------------------------------------------

    def _flush_open(self, *, with_arguments: bool) -> str:
        """A chunk per choice that still holds text (or arguments): the stream is ending."""
        indexes = {key[0] for key in self._text.open_keys()}
        if with_arguments:
            indexes |= {key[0] for key in self._arguments.open_keys()}
        out = []
        for index in sorted(indexes):
            delta: dict[str, Any] = {}
            if self._finish(index, delta, with_arguments=with_arguments):
                choice = {"index": index, "delta": delta, "finish_reason": None}
                out.append(format_event(None, dumps({**self._envelope, "choices": [choice]})))
        return "".join(out)

    @staticmethod
    def _only_empty_deltas(chunk: dict[str, Any]) -> bool:
        choices = chunk["choices"]
        if not set(chunk) <= _ENVELOPE_KEYS:
            return False  # an extra top-level field (obfuscation...) is always sent
        if not choices or not _empty(chunk.get("usage")):
            return False
        for choice in choices:
            if not isinstance(choice, dict) or not set(choice) <= {
                "index",
                "delta",
                "finish_reason",
                "logprobs",
            }:
                return False
            if choice.get("finish_reason") is not None or choice.get("logprobs") is not None:
                return False
            delta = choice.get("delta")
            if not isinstance(delta, dict) or not all(_empty(v) for v in delta.values()):
                return False
        return True


__all__ = ["OpenAIChatStream"]
