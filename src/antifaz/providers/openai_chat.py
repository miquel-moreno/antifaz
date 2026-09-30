"""OpenAI Chat Completions: which strings are masked in the request and restored in the answer.

Request (ADR-0013): every string of the body, known field or not, goes into ONE mask() call, in
a stable walk order. Tool-call `arguments` are JSON inside a string: they are parsed and their
string values masked. Object keys are schema and are never changed: if masking would change a
key, the request is blocked. Attachments (images, audio, files, file ids) are blocked.

Answer (ADR-0006): only `message.content`, `message.refusal` and the tool-call arguments are
restored; everything else passes untouched.

Forbidden: Never send a string that did not go through mask(). Never restore unknown fields.
"""

import json
from collections.abc import Callable, Iterator
from typing import Any

from antifaz.detect.scan import scan
from antifaz.errors import AttachmentBlocked, UnmaskableField
from antifaz.mask import Detector, mask
from antifaz.policy import DEFAULT_POLICY, Policy
from antifaz.restore import restore
from antifaz.vault import Vault

# A key or a part type that means "this is not text": it is blocked, never forwarded.
ATTACHMENT_KEYS = frozenset({"image_url", "input_audio", "file", "file_id", "file_data", "audio"})
ATTACHMENT_TYPES = frozenset({"image_url", "input_image", "input_audio", "input_file", "file"})
_JSON_STRING_KEYS = frozenset({"arguments"})


def _parse_container(text: str) -> Any | None:
    """The JSON object or array inside `text`, or None if it is not one."""
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict | list) else None


def _check_attachments(node: Any) -> None:
    if isinstance(node, dict):
        if ATTACHMENT_KEYS.intersection(node) or node.get("type") in ATTACHMENT_TYPES:
            raise AttachmentBlocked()
        for value in node.values():
            _check_attachments(value)
    elif isinstance(node, list):
        for item in node:
            _check_attachments(item)


def _walk(
    node: Any, text: Callable[[str], str], key: Callable[[str], str], *, nested: bool = False
) -> Any:
    """Rebuild `node` passing every string value through `text` and every key through `key`.

    The same order is used to collect the strings and to put them back. `nested` is True
    inside parsed arguments: a string there is user data, never parsed again.
    """
    if isinstance(node, str):
        return text(node)
    if isinstance(node, list):
        return [_walk(item, text, key, nested=nested) for item in node]
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for name, value in node.items():
            new_name = key(name)
            parsed = None
            if not nested and name in _JSON_STRING_KEYS and isinstance(value, str):
                parsed = _parse_container(value)
            if parsed is not None:
                walked = _walk(parsed, text, key, nested=True)
                out[new_name] = json.dumps(walked, ensure_ascii=False)
            else:
                out[new_name] = _walk(value, text, key, nested=nested)
        return out
    return node  # numbers, booleans and null pass as they are


def mask_request(
    body: dict[str, Any],
    *,
    policy: Policy = DEFAULT_POLICY,
    detector: Detector = scan,
) -> tuple[dict[str, Any], Vault]:
    """Masked copy of a Chat Completions body and the table of this request."""
    _check_attachments(body)
    collected: list[str] = []

    def collect(value: str) -> str:
        collected.append(value)
        return value

    _walk(body, collect, collect)
    result = mask(collected, policy, detector=detector)
    pairs: Iterator[tuple[str, str]] = zip(collected, result.texts, strict=True)

    def put_text(_: str) -> str:
        return next(pairs)[1]

    def put_key(_: str) -> str:
        original, masked = next(pairs)
        if masked != original:
            raise UnmaskableField()
        return original

    return _walk(body, put_text, put_key), result.vault


def _restore_arguments(arguments: str, vault: Vault) -> str:
    parsed = _parse_container(arguments)
    if parsed is None:
        return restore(arguments, vault)
    restored = _walk(parsed, lambda value: restore(value, vault), lambda name: name, nested=True)
    return json.dumps(restored, ensure_ascii=False)


def _restore_tool_calls(message: dict[str, Any], vault: Vault) -> None:
    calls = message.get("tool_calls")
    functions = (
        [call.get("function") for call in calls if isinstance(call, dict)]
        if (isinstance(calls, list))
        else []
    )
    functions.append(message.get("function_call"))  # legacy single call
    for function in functions:
        if isinstance(function, dict) and isinstance(function.get("arguments"), str):
            function["arguments"] = _restore_arguments(function["arguments"], vault)


def restore_response(body: dict[str, Any], vault: Vault) -> dict[str, Any]:
    """The answer with this request's placeholders restored in the known text fields."""
    out: dict[str, Any] = json.loads(
        json.dumps(body)
    )  # deep copy: the caller's object is not changed
    choices = out.get("choices")
    if not isinstance(choices, list):
        return out
    for choice in choices:
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(message, dict):
            continue
        for field in ("content", "refusal"):
            if isinstance(message.get(field), str):
                message[field] = restore(message[field], vault)
        _restore_tool_calls(message, vault)
    return out


__all__ = ["ATTACHMENT_KEYS", "ATTACHMENT_TYPES", "mask_request", "restore_response"]
