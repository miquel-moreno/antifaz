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
import re
from collections.abc import Callable, Iterator
from typing import Any

from antifaz.detect.scan import scan
from antifaz.errors import AttachmentBlocked, UnmaskableField
from antifaz.mask import Detector, mask
from antifaz.policy import DEFAULT_POLICY, Policy
from antifaz.restore import restore
from antifaz.vault import Vault

# Allowlist (ADR-0013): an object with a "type" may only have one of these types. Anything else
# (image, document, input_image, a type added tomorrow...) is blocked, never forwarded.
ALLOWED_TYPES = frozenset({"text", "refusal", "function"})
# Keys that carry non-text content: blocked wherever they appear.
ATTACHMENT_KEYS = frozenset(
    {
        "source",
        "data",
        "image",
        "document",
        "image_url",
        "input_audio",
        "file",
        "file_id",
        "file_data",
        "audio",
    }
)
# Tool definitions and output formats are JSON schemas written by the developer: their "type"
# values ("object", "string"...) and property names ("data", "file"...) are not content.
SCHEMA_FIELDS = frozenset({"tools", "functions", "response_format", "tool_choice"})
# Base64 inside a text part (a data: URL) is a binary in disguise: the detector cannot read it.
_DATA_URL = re.compile(r"data:[^,\s]{0,100};base64,", re.IGNORECASE)
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
        kind = node.get("type")
        if ATTACHMENT_KEYS.intersection(node) or (kind is not None and kind not in ALLOWED_TYPES):
            raise AttachmentBlocked()
        for value in node.values():
            _check_attachments(value)
    elif isinstance(node, list):
        for item in node:
            _check_attachments(item)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _walk(
    node: Any,
    text: Callable[[str], str],
    key: Callable[[str], str],
    *,
    number: Callable[[Any], Any] = lambda value: value,
    nested: bool = False,
) -> Any:
    """Rebuild `node` passing every string value through `text` and every key through `key`.

    The same order is used to collect the strings and to put them back. `nested` is True
    inside parsed arguments: a string there is user data, never parsed again.
    """
    if isinstance(node, str):
        return text(node)
    if isinstance(node, list):
        return [_walk(item, text, key, number=number, nested=nested) for item in node]
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for name, value in node.items():
            new_name = key(name)
            parsed = None
            if not nested and name in _JSON_STRING_KEYS and isinstance(value, str):
                parsed = _parse_container(value)
            if parsed is not None:
                walked = _walk(parsed, text, key, number=number, nested=True)
                out[new_name] = json.dumps(walked, ensure_ascii=False)
            else:
                out[new_name] = _walk(value, text, key, number=number, nested=nested)
        return out
    if _is_number(node):
        return number(node)
    return node  # booleans and null pass as they are


def mask_request(
    body: dict[str, Any],
    *,
    policy: Policy = DEFAULT_POLICY,
    detector: Detector = scan,
) -> tuple[dict[str, Any], Vault]:
    """Masked copy of a Chat Completions body and the table of this request."""
    if ATTACHMENT_KEYS.intersection(body):
        raise AttachmentBlocked()
    for name, value in body.items():
        if name not in SCHEMA_FIELDS:
            _check_attachments(value)
    collected: list[str] = []

    def collect(value: str) -> str:
        if _DATA_URL.search(value):
            raise AttachmentBlocked()
        collected.append(value)
        return value

    def collect_number(value: Any) -> Any:
        collected.append(str(value))
        return value

    _walk(body, collect, collect, number=collect_number)
    result = mask(collected, policy, detector=detector)
    pairs: Iterator[tuple[str, str]] = zip(collected, result.texts, strict=True)

    def put_text(_: str) -> str:
        return next(pairs)[1]

    def put_key(_: str) -> str:
        original, masked = next(pairs)
        if masked != original:
            raise UnmaskableField()
        return original

    def put_number(value: Any) -> Any:
        # A number cannot hold a placeholder: if it holds personal data, the request is blocked.
        put_key(str(value))
        return value

    return _walk(body, put_text, put_key, number=put_number), result.vault


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


__all__ = ["ALLOWED_TYPES", "ATTACHMENT_KEYS", "mask_request", "restore_response"]
