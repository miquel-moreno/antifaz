"""Every fixture is clean: no keys, no auth headers, no personal data, and a known provenance.

Runs over every .json and .sse file in tests/contract/fixtures, so a recording added later
(5d, second phase) is checked the same way before it can be committed with a real value.
"""

import contextlib
import json
import math
import re
from collections import Counter
from pathlib import Path

import pytest

from antifaz.detect.scan import scan
from tests.contract.conftest import FIXTURES

FIXTURE_FILES = sorted(p for p in FIXTURES.iterdir() if p.suffix in (".json", ".sse"))
PROVENANCE = re.compile(r"hand-written|recorded \d{4}-\d{2}-\d{2} model [\w.:-]+")
SSE_PROVENANCE = re.compile(r": provenance: (.+)\n")

KEY_PATTERNS = (
    re.compile(r"sk-ant-", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bBearer\s+\S", re.IGNORECASE),
)
# A header as a JSON key ("x-api-key": ...) or as an HTTP line (x-api-key: ...).
_HEADER_NAMES = r"(?:authorization|x-api-key|api-key|cookie|set-cookie|openai-organization)"
AUTH_HEADERS = re.compile(
    rf"""["']{_HEADER_NAMES}["']\s*:|^\s*{_HEADER_NAMES}\s*:""", re.IGNORECASE | re.MULTILINE
)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<![\w.])(?:\+34[\s.-]?)?[6789]\d{2}[\s.-]?\d{3}[\s.-]?\d{3}(?![\w.])")
TOKEN = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
MIN_ENTROPY_BITS = 3.5  # per character; ids like "chatcmpl-contract-text-0001" stay below

# Synthetic values allowed in fixtures (invented, documented in fixtures/README.md).
SYNTHETIC_OPAQUE = frozenset(
    {"ZmFrZS1zaWduYXR1cmUtZm9yLWNvbnRyYWN0LXRlc3Rz"}  # "fake-signature-for-contract-tests"
)
SYNTHETIC_PERSONAL = frozenset({"ana.garcia@example.com", "12345678Z"})


def _entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def _decoded(node: object, keys: list[str], strings: list[str]) -> None:
    """Every string and key of a parsed JSON value, JSON inside strings included."""
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            strings.append(current)
            if current.lstrip()[:1] in ("{", "["):  # tool arguments: JSON inside a string
                with contextlib.suppress(ValueError):
                    stack.append(json.loads(current))
        elif isinstance(current, dict):
            keys.extend(current)
            strings.extend(current)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def decoded_texts(text: str) -> tuple[list[str], list[str]]:
    """(keys, strings) of the fixture once decoded: the whole file if it is JSON, and the data
    of each SSE event. So "\u0031" or "\u002d" escapes cannot hide a value from the scan."""
    keys: list[str] = []
    strings: list[str] = []
    candidates = [text] + [
        line[5:].strip() for line in text.splitlines() if line.startswith("data:")
    ]
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        _decoded(parsed, keys, strings)
    return keys, strings


def problems(text: str) -> list[str]:
    """What is wrong with a fixture, raw or decoded. Only kinds, never the matched value."""
    keys, strings = decoded_texts(text)
    found: list[str] = []
    for piece in [text, *strings]:
        for problem in _text_problems(piece):
            if problem not in found:
                found.append(problem)
    if any(re.fullmatch(_HEADER_NAMES, key.strip(), re.IGNORECASE) for key in keys):
        found.append("an auth or cookie header")
    return found


def _text_problems(text: str) -> list[str]:
    found = [f"key-like text ({p.pattern})" for p in KEY_PATTERNS if p.search(text)]
    if AUTH_HEADERS.search(text):
        found.append("an auth or cookie header")
    for token in TOKEN.findall(text):
        if token not in SYNTHETIC_OPAQUE and _entropy(token) >= MIN_ENTROPY_BITS:
            found.append("a long high-entropy token")
    for pattern, kind in ((EMAIL, "an email"), (PHONE, "a phone number")):
        if any(m not in SYNTHETIC_PERSONAL for m in pattern.findall(text)):
            found.append(kind)
    for span in scan(text):
        if text[span.start : span.end] not in SYNTHETIC_PERSONAL:
            found.append(f"personal data ({span.type})")
    return found


def provenance(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".sse":
        match = SSE_PROVENANCE.match(text)
        return match.group(1) if match else None
    value = json.loads(text).get("provenance")
    return value if isinstance(value, str) else None


def test_there_are_fixtures_for_both_providers() -> None:
    names = {p.name for p in FIXTURE_FILES}
    assert any(n.startswith("openai_") for n in names)
    assert any(n.startswith("anthropic_") for n in names)


def test_only_fixtures_and_the_readme_live_in_the_folder() -> None:
    others = {p.name for p in FIXTURES.iterdir()} - {p.name for p in FIXTURE_FILES}
    assert others == {"README.md"}


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.name)
def test_fixture_has_no_keys_headers_or_personal_data(path: Path) -> None:
    assert problems(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.name)
def test_fixture_says_where_it_comes_from(path: Path) -> None:
    value = provenance(path)
    assert value is not None and PROVENANCE.fullmatch(value)


@pytest.mark.parametrize("path", [p for p in FIXTURE_FILES if p.suffix == ".json"],
                         ids=lambda p: p.name)  # fmt: skip
def test_json_fixture_has_status_and_body(path: Path) -> None:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert set(fixture) == {"provenance", "status", "body"}
    assert isinstance(fixture["status"], int)


# --- The scanner itself catches what it must (so a clean result means something) -----------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ('{"message": "sk-proj-AbCdEf0123456789xyz"}', "key-like"),
        ('{"message": "sk-ant-api03-something"}', "key-like"),
        ('{"auth": "Bearer abc.def"}', "key-like"),
        ('{"headers": {"x-api-key": "whatever"}}', "header"),
        ('{"headers": {"Authorization": "whatever"}}', "header"),
        ("set-cookie: session=abc", "header"),
        ('{"sig": "q9Xz7LmP2vR8tK4wN6yB1cF5hJ3dG0sA"}', "high-entropy"),
        ('{"text": "escribe a pepe.perez@empresa.es"}', "email"),
        ('{"text": "llama al 699 123 456"}', "phone"),
        ('{"text": "DNI 87654321X"}', "ES_DNI"),
        ('{"text": "IBAN ES91 2100 0418 4502 0005 1332"}', "IBAN"),
        # Escaped in JSON: only the decoded string shows the value.
        (r'{"text": "DNI 87654321X"}', "ES_DNI"),
        (r'{"text": "escribe a pepe.perez@empresa.es"}', "email"),
        (r'{"auth": "Bearer abc.def"}', "key-like"),
        (r'{"headers": {"x-api-key": "whatever"}}', "header"),
        (r'{"message": "sk-proj-AbCdEf0123456789xyz"}', "key-like"),
        ('data: {"delta": {"text": "DNI 8765432\u0031X"}}', "ES_DNI"),
        (r'{"arguments": "{\"dni\": \"8765432\u0031X\"}"}', "ES_DNI"),
    ],
)
def test_scanner_catches_leaks(text: str, kind: str) -> None:
    assert any(kind in problem for problem in problems(text))


def test_scanner_accepts_placeholders_and_synthetic_examples() -> None:
    text = (
        '{"text": "[[ES_DNI_1]] [[EMAIL_1]] 12345678Z ana.garcia@example.com msg_contract_0001",'
        ' "message": "invalid x-api-key"}'
    )
    assert problems(text) == []
