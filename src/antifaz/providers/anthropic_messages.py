"""Anthropic Messages: which strings are masked in the request and restored in the answer.

Request (ADR-0013): every string of the body (system, messages, tool inputs and results, tool
descriptions, metadata, unknown fields) goes into ONE mask() call (json_walk). Content blocks
follow an allowlist: `text`, `tool_use`, `tool_result` (with text inside) and the reasoning
blocks. Anything else (image, document, search_result, container_upload, server tools, a type
added tomorrow...) is blocked.

Reasoning blocks (`thinking`, `redacted_thinking`) of `messages[].content` are copied untouched
(invariant 9): the signature covers them. They came from the model, which only saw masked text,
so they hold placeholders, never values. They are still scanned: if one holds a value to hide
(a client edited it), the request is blocked, because it cannot be masked.

Answer (ADR-0006): only `text` blocks and `tool_use.input` are restored; reasoning blocks and
everything else pass untouched.

Forbidden: Never change a reasoning block. Never restore unknown fields.
"""

import json
from typing import Any

from antifaz.detect.scan import scan
from antifaz.errors import AttachmentBlocked, UnmaskableField
from antifaz.mask import Detector, mask
from antifaz.policy import DEFAULT_POLICY, Policy
from antifaz.providers.json_walk import (
    ATTACHMENT_KEYS,
    check_attachments,
    check_depth,
    mask_body,
    restore_strings,
    walk,
)
from antifaz.restore import placeholder_tokens, restore
from antifaz.vault import Vault

THINKING_TYPES = frozenset({"thinking", "redacted_thinking"})
# Allowlist of message content blocks (ADR-0013).
CONTENT_TYPES = frozenset({"text", "tool_use", "tool_result"}) | THINKING_TYPES
# Inside `system` and `tool_result.content` only text blocks are allowed.
TEXT_ONLY = frozenset({"text"})
# Exact keys of the reasoning blocks: nothing else can travel unmasked inside them.
THINKING_KEYS = {
    "thinking": frozenset({"type", "thinking", "signature"}),
    "redacted_thinking": frozenset({"type", "data"}),
}
# Request settings: only these keys (their "type" values are settings, not content).
CONFIG_KEYS = {
    "thinking": frozenset({"type", "budget_tokens"}),
    "tool_choice": frozenset({"type", "name", "disable_parallel_tool_use"}),
}
_NOTHING: frozenset[str] = frozenset()


def _check_block(block: Any, allowed: frozenset[str]) -> None:
    if not isinstance(block, dict):
        check_attachments(block, _NOTHING)
        return
    kind = block.get("type")
    if kind not in allowed:
        raise AttachmentBlocked()
    if kind in THINKING_TYPES:
        # Copied untouched; its text is scanned in _check_thinking. `signature` and `data`
        # are opaque and cannot be inspected (accepted risk, ADR-0013).
        if not set(block) <= THINKING_KEYS[kind] or not all(
            isinstance(value, str) for value in block.values()
        ):
            raise AttachmentBlocked()
        return
    if ATTACHMENT_KEYS.intersection(block):
        raise AttachmentBlocked()
    for name, value in block.items():
        if name == "cache_control":
            continue  # a cache setting: masked, not an attachment
        if kind == "tool_use" and name == "input":
            check_attachments(value, None)  # data for the tool: any "type", no attachments
            continue
        if kind == "tool_result" and name == "content" and isinstance(value, list):
            for item in value:
                _check_block(item, TEXT_ONLY)
            continue
        check_attachments(value, _NOTHING)


def _check_content(content: Any, allowed: frozenset[str]) -> None:
    if isinstance(content, list):
        for block in content:
            _check_block(block, allowed)
    else:
        check_attachments(content, _NOTHING)


def _check_schema(node: Any) -> None:
    """JSON schema of a tool: property names like "data" are fine; base64 objects are not."""
    if isinstance(node, dict):
        if node.get("type") == "base64":  # not a JSON Schema type: an embedded binary
            raise AttachmentBlocked()
        for value in node.values():
            _check_schema(value)
    elif isinstance(node, list):
        for item in node:
            _check_schema(item)


def _check_tools(tools: Any) -> None:
    if not isinstance(tools, list):
        check_attachments(tools, None)
        return
    for tool in tools:
        if not isinstance(tool, dict):
            check_attachments(tool, None)
            continue
        for name, value in tool.items():
            if name in ATTACHMENT_KEYS:
                raise AttachmentBlocked()
            if name == "input_schema":
                _check_schema(value)
            else:
                check_attachments(value, None)


def _check(body: dict[str, Any]) -> None:
    if ATTACHMENT_KEYS.intersection(body):
        raise AttachmentBlocked()
    for name, value in body.items():
        if name in CONFIG_KEYS:
            if isinstance(value, dict) and not set(value) <= CONFIG_KEYS[name]:
                raise AttachmentBlocked()
            check_attachments(value, None)
        elif name == "tools":
            _check_tools(value)
        elif name == "system":
            _check_content(value, TEXT_ONLY)
        elif name == "messages" and isinstance(value, list):
            for message in value:
                if not isinstance(message, dict):
                    check_attachments(message, _NOTHING)
                    continue
                for field, item in message.items():
                    allowed = CONTENT_TYPES if field == "content" else _NOTHING
                    _check_content(item, allowed)
        else:
            check_attachments(value, _NOTHING)


def _is_thinking(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") in THINKING_TYPES


def _split_thinking(
    body: dict[str, Any],
) -> tuple[dict[str, Any], dict[tuple[int, int], Any]]:
    """A copy of `body` with each reasoning block of messages[].content replaced by None."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body, {}
    kept: dict[tuple[int, int], Any] = {}
    new_messages: list[Any] = []
    for i, message in enumerate(messages):
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list) and any(_is_thinking(block) for block in content):
            new_content = []
            for j, block in enumerate(content):
                if _is_thinking(block):
                    kept[(i, j)] = block
                    block = None
                new_content.append(block)
            message = {**message, "content": new_content}
        new_messages.append(message)
    return {**body, "messages": new_messages}, kept


def _check_thinking(blocks: list[Any], policy: Policy, detector: Detector) -> None:
    """Block if a reasoning block holds a value to hide: it cannot be masked."""
    texts: list[str] = []

    def collect(value: str) -> str:
        texts.append(value)
        return value

    walk(blocks, collect, collect)
    if texts and len(mask(texts, policy, detector=detector).vault):
        raise UnmaskableField()


def mask_request(
    body: dict[str, Any],
    *,
    policy: Policy = DEFAULT_POLICY,
    detector: Detector = scan,
) -> tuple[dict[str, Any], Vault]:
    """Masked copy of a Messages (or count_tokens) body and the table of this request."""
    check_depth(body)  # before any recursive walk
    _check(body)
    without_thinking, kept = _split_thinking(body)
    _check_thinking(list(kept.values()), policy, detector)
    # Placeholders kept in reasoning blocks come from an earlier turn: a new value must not
    # get the same token, or restore would put the wrong value (ADR-0013).
    reserved = frozenset().union(
        *(placeholder_tokens(block.get("thinking", "")) for block in kept.values())
    )
    masked, vault = mask_body(without_thinking, policy=policy, detector=detector, reserved=reserved)
    for (i, j), block in kept.items():
        masked["messages"][i]["content"][j] = block  # the original object, untouched
    return masked, vault


def restore_response(body: dict[str, Any], vault: Vault) -> dict[str, Any]:
    """The answer with this request's placeholders restored in text and tool inputs."""
    out: dict[str, Any] = json.loads(json.dumps(body))  # deep copy: the caller's is not changed
    content = out.get("content")
    if not isinstance(content, list):
        return out
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            block["text"] = restore(block["text"], vault)
        elif kind == "tool_use" and isinstance(block.get("input"), dict | list):
            block["input"] = restore_strings(block["input"], vault)
    return out


__all__ = ["CONTENT_TYPES", "mask_request", "restore_response"]
