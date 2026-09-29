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
        # Found by the privacy review of issue 1:
        "cp .env x.txt",
        "base64 .env",
        "cat .env*",
        "cat .en?",
        "python -c \"print(open('.env').read())\"",
        "Get-Content .env",
        "export",
        "git -C . push --force",
        "git -c core.hooksPath=/dev/null commit -m x",
        "SKIP=gitleaks git commit -m x",
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
        "ls docs/.environment-notes",
        "git -C ../antifaz push -u origin main",
        "export UV_NO_DEV=1",
    ],
)
def test_normal_commands_pass(command: str) -> None:
    assert verdict(command) == 0


def test_unreadable_input_fails_closed() -> None:
    assert run_hook("not json").returncode == 2


def test_the_shell_hook_also_watches_powershell() -> None:
    settings = json.loads((PRE_BASH.parents[1] / "settings.json").read_text(encoding="utf-8"))
    matchers = [entry["matcher"] for entry in settings["hooks"]["PreToolUse"]]

    assert any({"Bash", "PowerShell"} <= set(m.split("|")) for m in matchers)
