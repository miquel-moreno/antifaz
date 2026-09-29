"""PreToolUse hook: blocks shell commands that expose secrets or skip checks.

Claude Code sends the tool call as JSON on stdin (Bash and PowerShell tools). Exit
code 2 blocks the command and shows stderr to Claude as the reason. This hook fails
closed: if it cannot read its input, it blocks.

It is defence in depth, not a barrier: anyone able to run arbitrary code can find a
way around a list of patterns. The real protection is that secrets are not in the
repository and gitleaks checks every change in CI.
"""

import json
import re
import sys

# Any mention of a .env file (except .env.example), whatever the command: cp, base64,
# python -c "open('.env')"... Also shell globs that could match it (.env*, .en?).
ENV_MENTION = r"(^|[^\w.-])\.env(?![\w-]|\.example\b)"
ENV_GLOB = r"(^|[^\w.-])\.e[\w]{0,2}[?*\[]"
# git with global options before the subcommand: git -C dir push, git -c key=value commit
GIT = r"\bgit(\s+-[Cc]\s+\S+)*\s+"

BLOCKED = [
    (ENV_MENTION, "Touching .env files is not allowed: secrets stay out of the session."),
    (ENV_GLOB, "Wildcards that could match .env files are not allowed."),
    (
        r"(^|[\s;&|(])(printenv|get-childitem\s+env:|dir\s+env:|gci\s+env:)(\s|$|[|;&)])",
        "Printing environment variables is not allowed.",
    ),
    (
        r"(^|[;&|(]\s*|^\s*)(env|set|export|export\s+-p)\s*($|[|;&)>])",
        "Printing environment variables is not allowed.",
    ),
    (
        r"\becho\b[^|;&]*\$\{?[A-Z0-9_]*(KEY|TOKEN|SECRET|PASSWORD|PASS)[A-Z0-9_]*",
        "Printing secret variables is not allowed.",
    ),
    (r"\$env:[A-Z0-9_]*(KEY|TOKEN|SECRET|PASSWORD)", "Printing secret variables is not allowed."),
    (
        r"\bdocker(-|\s+)compose\b[^|;&]*\bconfig\b",
        "docker compose config prints resolved secrets; not allowed.",
    ),
    (r"--no-verify\b", "Skipping git hooks (--no-verify) is not allowed. Fix the check instead."),
    (r"core\.hookspath", "Changing core.hooksPath skips the git hooks; not allowed."),
    (r"(^|[\s;&|(])SKIP=", "SKIP= skips pre-commit hooks; not allowed. Fix the check instead."),
    (
        rf"{GIT}push\b[^|;&]*(\s--force(-with-lease)?\b|\s-f\b|\s\+[\w./-]+)",
        "Force push is not allowed from Claude Code. Miquel runs it himself if ever needed.",
    ),
    (
        rf"{GIT}config\s+--global\b",
        "Changing global git config is not allowed; use -c user.name/-c user.email per commit.",
    ),
    (r"\brm\s+-[a-z]*r[a-z]*\s+(/|~|\$HOME)(\s|$)", "Dangerous delete blocked."),
]


def strip_messages(command: str) -> str:
    """Remove commit/PR message arguments so text inside them is not matched."""
    return re.sub(
        r"(\s(-m|--message|--title|--body|--notes)\s+)('[^']*'|\"[^\"]*\")", r"\1''", command
    )


def main() -> int:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        payload = json.loads(raw)
        command = str(payload.get("tool_input", {}).get("command", ""))
    except Exception as exc:  # fail closed
        print(
            f"Blocked by .claude/hooks/pre_bash.py: could not read the command ({exc}).",
            file=sys.stderr,
        )
        return 2

    checked = strip_messages(command)
    for pattern, reason in BLOCKED:
        if re.search(pattern, checked, flags=re.IGNORECASE):
            print(f"Blocked by .claude/hooks/pre_bash.py: {reason}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
