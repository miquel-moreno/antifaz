"""Shared pieces of the provider formats (ADR-0013): the JSON walk, the checks and one mask().

Request: every string of the body, known field or not, goes into ONE mask() call, in a stable
walk order. Object keys are schema and are never changed: if masking would change a key, the
request is blocked. Numbers go through the detector as text and block if they hold data.

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

# Keys that carry non-text content: blocked wherever the format checks for attachments.
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
# Base64 inside a text (a data: URL) is a binary in disguise: the detector cannot read it.
_DATA_URL = re.compile(r"data:[^,\s]{0,100};base64,", re.IGNORECASE)
_NO_KEYS: frozenset[str] = frozenset()


def parse_container(text: str) -> Any | None:
    """The JSON object or array inside `text`, or None if it is not one."""
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict | list) else None


def check_attachments(node: Any, allowed_types: frozenset[str]) -> None:
    """Block attachment keys and any object whose "type" is not in `allowed_types`."""
    if isinstance(node, dict):
        kind = node.get("type")
        if ATTACHMENT_KEYS.intersection(node) or (kind is not None and kind not in allowed_types):
            raise AttachmentBlocked()
        for value in node.values():
            check_attachments(value, allowed_types)
    elif isinstance(node, list):
        for item in node:
            check_attachments(item, allowed_types)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def walk(
    node: Any,
    text: Callable[[str], str],
    key: Callable[[str], str],
    *,
    number: Callable[[Any], Any] = lambda value: value,
    json_string_keys: frozenset[str] = _NO_KEYS,
    nested: bool = False,
) -> Any:
    """Rebuild `node` passing every string value through `text` and every key through `key`.

    The same order is used to collect the strings and to put them back. A string under one of
    `json_string_keys` that holds a JSON object or array is parsed and walked (`nested` is then
    True: a string there is user data, never parsed again) and serialized back.
    """
    if isinstance(node, str):
        return text(node)
    if isinstance(node, list):
        return [
            walk(item, text, key, number=number, json_string_keys=json_string_keys, nested=nested)
            for item in node
        ]
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for name, value in node.items():
            new_name = key(name)
            parsed = None
            if not nested and name in json_string_keys and isinstance(value, str):
                parsed = parse_container(value)
            if parsed is not None:
                walked = walk(parsed, text, key, number=number, nested=True)
                out[new_name] = json.dumps(walked, ensure_ascii=False)
            else:
                out[new_name] = walk(
                    value,
                    text,
                    key,
                    number=number,
                    json_string_keys=json_string_keys,
                    nested=nested,
                )
        return out
    if _is_number(node):
        return number(node)
    return node  # booleans and null pass as they are


def mask_body(
    body: Any,
    *,
    policy: Policy = DEFAULT_POLICY,
    detector: Detector = scan,
    json_string_keys: frozenset[str] = _NO_KEYS,
) -> tuple[Any, Vault]:
    """Masked copy of `body` (strings masked, keys and numbers checked) and its table.

    One mask() call for the whole body. The caller checks attachments first.
    """
    collected: list[str] = []

    def collect(value: str) -> str:
        if _DATA_URL.search(value):
            raise AttachmentBlocked()
        collected.append(value)
        return value

    def collect_number(value: Any) -> Any:
        collected.append(str(value))
        return value

    walk(body, collect, collect, number=collect_number, json_string_keys=json_string_keys)
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

    masked_body = walk(
        body, put_text, put_key, number=put_number, json_string_keys=json_string_keys
    )
    return masked_body, result.vault


def restore_strings(node: Any, vault: Vault) -> Any:
    """`node` with this request's placeholders restored in every string value (keys unchanged)."""
    return walk(node, lambda value: restore(value, vault), lambda name: name, nested=True)


__all__ = [
    "ATTACHMENT_KEYS",
    "check_attachments",
    "mask_body",
    "parse_container",
    "restore_strings",
    "walk",
]
