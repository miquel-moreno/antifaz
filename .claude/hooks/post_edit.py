"""PostToolUse hook: formats and lints every Python file Claude edits.

Uses the project's pinned ruff (uv run --frozen). Does nothing until the
project has a pyproject.toml. If ruff still reports problems after the
automatic fixes, exit code 2 sends them back to Claude as feedback (the edit
itself is already done).
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
    except Exception:
        return 0
    file_path = payload.get("tool_input", {}).get("file_path")
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR", payload.get("cwd", ".")))
    if not file_path or Path(file_path).suffix != ".py" or not Path(file_path).exists():
        return 0
    if not (project / "pyproject.toml").exists():
        return 0

    base = ["uv", "run", "--frozen", "--quiet", "ruff"]
    subprocess.run([*base, "format", file_path], cwd=project, capture_output=True, check=False)
    result = subprocess.run(
        [*base, "check", "--fix", file_path],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        print(
            f"ruff found problems in {file_path}:\n{result.stdout}{result.stderr}", file=sys.stderr
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
