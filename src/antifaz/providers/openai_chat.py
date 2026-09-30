"""OpenAI Chat Completions: which strings are masked in the request and restored in the answer.

Request (ADR-0013): every string of the body goes into ONE mask() call (json_walk). Tool-call
`arguments` are JSON inside a string: they are parsed and their string values masked.
Attachments (images, audio, files, file ids) are blocked.

Answer (ADR-0006): only `message.content`, `message.refusal` and the tool-call arguments are
restored; everything else passes untouched.

Forbidden: Never send a string that did not go through mask(). Never restore unknown fields.
"""

import json
from typing import Any

from antifaz.detect.scan import scan
from antifaz.errors import AttachmentBlocked
from antifaz.mask import Detector
from antifaz.policy import DEFAULT_POLICY, Policy
from antifaz.providers.json_walk import (
    ATTACHMENT_KEYS,
    check_attachments,
    mask_body,
    parse_container,
    restore_strings,
)
from antifaz.restore import restore
from antifaz.vault import Vault

# Allowlist (ADR-0013): an object with a "type" may only have one of these types. Anything else
# (image, document, input_image, a type added tomorrow...) is blocked, never forwarded.
ALLOWED_TYPES = frozenset({"text", "refusal", "function"})
# Tool definitions and output formats are JSON schemas written by the developer: their "type"
# values ("object", "string"...) and property names ("data", "file"...) are not content.
SCHEMA_FIELDS = frozenset({"tools", "functions", "response_format", "tool_choice"})
_JSON_STRING_KEYS = frozenset({"arguments"})


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
            check_attachments(value, ALLOWED_TYPES)
    masked, vault = mask_body(
        body, policy=policy, detector=detector, json_string_keys=_JSON_STRING_KEYS
    )
    return masked, vault


def _restore_arguments(arguments: str, vault: Vault) -> str:
    parsed = parse_container(arguments)
    if parsed is None:
        return restore(arguments, vault)
    return json.dumps(restore_strings(parsed, vault), ensure_ascii=False)


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
