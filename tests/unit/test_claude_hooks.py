"""The Claude Code hooks in .claude/hooks block what they promise to block."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

PRE_BASH = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "pre_bash.py"


def run_hook(stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, str(PRE_BASH)],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def verdict(command: str) -> int:
    return run_hook(json.dumps({"tool_input": {"command": command}})).returncode


@pytest.mark.parametrize(
    "command",
    [
        "cat .env",
        "type .env.local",
        "grep KEY .env",
        "printenv",
        "env",
        "echo $OPENAI_API_KEY",
        "docker compose config",
        'git commit --no-verify -m "x"',
        "git push --force origin main",
        "git push -f",
        "git push origin +main",
        "git config --global user.name x",
        "rm -rf ~",
    ],
)
def test_dangerous_commands_are_blocked(command: str) -> None:
    assert verdict(command) == 2


@pytest.mark.parametrize(
    "command",
    [
        "cat .env.example",
        "uv run pytest -q",
        "git push -u origin feat/validators",
        'git commit -m "docs: explain why cat .env is blocked"',
        "make check",
    ],
)
def test_normal_commands_pass(command: str) -> None:
    assert verdict(command) == 0


def test_unreadable_input_fails_closed() -> None:
    assert run_hook("not json").returncode == 2
