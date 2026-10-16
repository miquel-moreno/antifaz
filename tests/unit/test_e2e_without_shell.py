"""The e2e tests never need a shell or coreutils inside the image (issue 54).

The image may move to a base without `sh` (distroless). A check that runs `sh -c ...` there
does not fail clearly: `command -v curl` without `sh` prints nothing, which is what "no curl"
looked like. So every check inside the container uses the image's `python`
(tests/e2e/image_probes.py), and this test fails if a shell or a coreutil comes back.

It reads the string values of the Python sources (dict keys and subscripts are names of JSON
fields, not commands) and the YAML files of tests/e2e. ABSENT_SHELLS and ABSENT_TOOLS are the
exceptions: they list the commands the image must NOT have.
"""

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
E2E = REPO / "tests" / "e2e"

# A string that is exactly one of these is a command run inside the container.
SHELL_COMMANDS = frozenset(
    {"sh", "bash", "ash", "dash", "/bin/sh", "/bin/bash", "id", "ls", "find", "cat", "which"}
)
# Inside a longer string (a `-c` script, a healthcheck, a Compose test).
SHELL_PATTERNS = (
    re.compile(r"\b(?:ba|da)?sh\s+-c\b"),
    re.compile(r"\b(?:ba|da)?sh[\"']?\s*,\s*[\"']?-c\b"),  # ["sh", "-c", ...] in YAML
    re.compile(r"\bcommand\s+-v\b"),
    re.compile(r"CMD-SHELL"),
    re.compile(r"/bin/(?:ba|da)?sh\b"),
)
ALLOWED_LISTS = frozenset({"ABSENT_SHELLS", "ABSENT_TOOLS"})


def _skipped_nodes(tree: ast.AST) -> set[int]:
    """ids of the string nodes that are not commands: dict keys, subscripts, ABSENT_TOOLS."""
    skipped: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            skipped |= {id(key) for key in node.keys if key is not None}
        elif isinstance(node, ast.Subscript):
            skipped |= {id(sub) for sub in ast.walk(node.slice)}
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {t.id for t in targets if isinstance(t, ast.Name)}
            if names & ALLOWED_LISTS and node.value is not None:
                skipped |= {id(sub) for sub in ast.walk(node.value)}
    return skipped


def shell_uses_in_python(source: str) -> list[str]:
    """Each string value of `source` that runs a shell or a coreutil, with its line."""
    tree = ast.parse(source)
    skipped = _skipped_nodes(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skipped:
            continue
        text = node.value
        if text.strip() in SHELL_COMMANDS or any(p.search(text) for p in SHELL_PATTERNS):
            found.append(f"line {node.lineno}: {text[:60]!r}")
    return found


def shell_uses_in_text(text: str) -> list[str]:
    return [
        f"line {number}: {line.strip()[:60]!r}"
        for number, line in enumerate(text.splitlines(), start=1)
        if any(p.search(line) for p in SHELL_PATTERNS)
    ]


@pytest.mark.parametrize("path", sorted(E2E.glob("*.py")), ids=lambda p: p.name)
def test_no_e2e_python_file_runs_a_shell_in_the_container(path: Path) -> None:
    assert shell_uses_in_python(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("path", sorted(E2E.glob("*.yml")), ids=lambda p: p.name)
def test_no_e2e_compose_file_runs_a_shell(path: Path) -> None:
    assert shell_uses_in_text(path.read_text(encoding="utf-8")) == []


def test_there_are_e2e_files_to_check() -> None:
    names = {p.name for p in E2E.glob("*.py")} | {p.name for p in E2E.glob("*.yml")}
    assert {"test_compose.py", "test_image_cli.py", "docker-compose.e2e.yml"} <= names


@pytest.mark.parametrize(
    "source",
    [
        'docker("run", "--rm", "--entrypoint", "sh", IMAGE, "-c", "true")',
        'stack.compose("exec", "-T", "antifaz", "id", "-u")',
        'docker("exec", name, "id", "-u")',
        'docker("run", "--rm", "--entrypoint", "ls", IMAGE, "-a", "/app")',
        'docker("run", "--rm", IMAGE, "find", "/", "-name", "x")',
        'docker("run", "--rm", IMAGE, "cat", "/etc/passwd")',
        'docker("run", "--rm", IMAGE, "which", "curl")',
        'docker("run", "--rm", IMAGE, "/bin/bash", "-lc", "x")',
        'SCRIPT = "command -v curl wget pip"',
        'command = ["bash", "-c", "echo"]',
        "test = \"sh -c 'curl localhost'\"",
        'OTHER_LIST = ("sh",)',
    ],
)
def test_the_check_finds_a_shell_or_a_coreutil(source: str) -> None:
    assert shell_uses_in_python(source) != []


@pytest.mark.parametrize(
    "source",
    [
        'body = {"id": "chatcmpl-e2e"}',
        'event["id"]',
        'ABSENT_TOOLS = ("curl", "sh", "bash", "which")',
        'ABSENT_SHELLS = ("sh", "bash", "dash", "busybox")',
        'docker("run", "--rm", "--entrypoint", "python", IMAGE, "-c", "import os")',
        '"""A docstring that says the image has no sh, no id and no ls."""',
    ],
)
def test_the_check_lets_json_fields_and_the_absent_tools_through(source: str) -> None:
    assert shell_uses_in_python(source) == []


@pytest.mark.parametrize(
    "text",
    [
        "      test: [CMD-SHELL, curl -f http://localhost]",
        '      test: ["CMD", "sh", "-c", "wget -q localhost"]',
        "      entrypoint: /bin/sh",
    ],
)
def test_the_check_finds_a_shell_in_a_compose_file(text: str) -> None:
    assert shell_uses_in_text(text) != []
