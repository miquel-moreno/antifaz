"""`antifaz setup claude-code`: point Claude Code at Antifaz, never writing a key (issue 31).

ADR-0017. What it changes, and nothing else:

- `env.ANTHROPIC_BASE_URL` (an http(s) URL without user, password, query or fragment), and
- `apiKeyHelper`, only when the user passes --key-helper (a command Claude Code runs to get
  the key; refused if it looks like it holds a key itself).

The key goes in the ANTHROPIC_AUTH_TOKEN environment variable (sent as `Authorization:
Bearer`), never in a settings file: this command never reads it and never writes it.

Target: the user settings file (`$CLAUDE_CONFIG_DIR/settings.json`, otherwise
`~/.claude/settings.json`; `%USERPROFILE%\\.claude` on Windows) or, with --project,
`.claude/settings.local.json` in the current folder. The shared `.claude/settings.json` of a
repository is refused: it is committed and shared.

Default is a dry run: it prints a diff and the SHA-256 of the file, and writes nothing. In the
diff every value is hidden except the two this command owns (and the old base URL or helper
only when it holds no user, password, query or anything key-like). With --apply, after typing
`yes` (or --yes): the file is read again and must still have the same SHA-256 ("file changed,
run again" otherwise), a dated backup is written (O_EXCL, 0600, never over another one), the
new file goes to a 0600 temp file in the same folder, is parsed back, and replaces the old one
with `os.replace` (retried on Windows). A UTF-8 BOM and CRLF line ends are kept; the JSON is
written with 2-space indent. Anything unreadable (not UTF-8, invalid JSON, a key twice in an
object, NaN, a top level or an `env` that is not an object, a link) aborts without touching
anything.

Ownership without comments or a sidecar file: --uninstall removes `env.ANTHROPIC_BASE_URL` only
if it still equals --url, and `apiKeyHelper` only if --key-helper is given and still equal;
otherwise it aborts and shows the diff. An `env` left empty is removed.

Exit codes: 0 done (or nothing to do, or a dry run); 2 usage or refused (nothing written);
1 the file changed meanwhile or could not be written (the old file is untouched).

Forbidden: reading or writing any key. Printing a value this command does not own.
"""

import argparse
import contextlib
import copy
import difflib
import hashlib
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from antifaz.cli import init
from antifaz.cli.doctor import DEFAULT_URL, _gateway_url

SETTINGS = "settings.json"
LOCAL_SETTINGS = "settings.local.json"
BASE_URL = "ANTHROPIC_BASE_URL"
HELPER = "apiKeyHelper"
CREDENTIAL_VARIABLES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
MAX_SETTINGS_BYTES = 1024 * 1024
MAX_HELPER = 1024
HIDDEN = "<hidden>"
_UTF8_BOM = b"\xef\xbb\xbf"
# Something a key helper command should never hold: a long hex or base64-like run, or a
# provider-style key. A real helper is a path or a short command line.
_KEY_LIKE = re.compile(r"[0-9a-fA-F]{32,}|[A-Za-z0-9_\-]{40,}|\bsk-[A-Za-z0-9_\-]{12,}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Environment variables whose values a helper must not contain (compared, never printed).
_SECRET_VARIABLES = ("ANTIFAZ_API_KEY", *CREDENTIAL_VARIABLES)
# Where Claude Code reads managed settings (https://code.claude.com/docs/en/managed-settings).
MANAGED_FOLDERS = {
    "darwin": "/Library/Application Support/ClaudeCode",
    "linux": "/etc/claude-code",
    "win32": r"C:\Program Files\ClaudeCode",
}


class SetupError(Exception):
    """A refusal with a message safe to print (never a value) and its exit code."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def _say(message: str) -> None:
    print(message)


def _warn(message: str) -> None:
    print(f"antifaz setup claude-code: {message}", file=sys.stderr)


def _ask(prompt: str) -> str:
    return input(prompt)


def _stdin_is_terminal() -> bool:
    return sys.stdin.isatty()


def _managed_folder() -> Path | None:
    folder = MANAGED_FOLDERS.get(sys.platform)
    return Path(folder) if folder else None


# --- Arguments --------------------------------------------------------------------------------


def add_parser(commands: "argparse._SubParsersAction[Any]") -> None:
    setup = commands.add_parser("setup", help="point a client at Antifaz (claude-code)")
    clients = setup.add_subparsers(dest="client", required=True)
    command = clients.add_parser(
        "claude-code",
        help="set ANTHROPIC_BASE_URL in Claude Code's settings (never a key); dry run unless "
        "--apply",
    )
    command.add_argument(
        "--url", default=DEFAULT_URL, help=f"Antifaz's address (default: {DEFAULT_URL})"
    )
    command.add_argument(
        "--project",
        action="store_true",
        help="write .claude/settings.local.json in the current folder instead of your user "
        "settings",
    )
    command.add_argument(
        "--key-helper",
        metavar="CMD",
        help="also set apiKeyHelper: a command Claude Code runs to get your Antifaz key "
        "(it runs through your shell)",
    )
    command.add_argument("--apply", action="store_true", help="write the change (asks first)")
    command.add_argument("--yes", action="store_true", help="with --apply, do not ask")
    command.add_argument(
        "--uninstall",
        action="store_true",
        help="remove what this command set, only if it is still unchanged",
    )


# --- Values -----------------------------------------------------------------------------------


def checked_url(value: str) -> str:
    """The URL without a trailing "/", or SetupError (without repeating it: it may hold a
    password)."""
    url = _gateway_url(value) if not _CONTROL.search(value) and " " not in value else None
    if url is None or "?" in value or "#" in value:
        raise SetupError(
            "--url expects http(s)://host[:port] without a user, password, query or fragment"
        )
    if _KEY_LIKE.search(url) or any(secret in url for secret in _secret_values()):
        raise SetupError("--url seems to contain a key; the settings file must never hold one")
    return url


def _is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in ("localhost", "::1") or host.startswith("127.")


def _secret_values() -> list[str]:
    return [
        value for name in _SECRET_VARIABLES if len(value := os.environ.get(name, "").strip()) >= 8
    ]


def checked_helper(value: str) -> str:
    """The helper command as given, or SetupError if it is empty, odd or seems to hold a key."""
    if not value.strip() or len(value) > MAX_HELPER or _CONTROL.search(value):
        raise SetupError(f"--key-helper expects one command line (at most {MAX_HELPER} characters)")
    if _KEY_LIKE.search(value) or any(secret in value for secret in _secret_values()):
        raise SetupError(
            "--key-helper seems to contain a key; give a command that reads the key from a "
            "file or a vault instead (the settings file must never hold a key)"
        )
    return value


def _shown_url(value: object) -> str:
    """A base URL found in the file, or HIDDEN if it could hold a secret."""
    if not isinstance(value, str) or _CONTROL.search(value) or _KEY_LIKE.search(value):
        return HIDDEN
    try:
        parts = urlsplit(value)
    except ValueError:
        return HIDDEN
    if "@" in parts.netloc or "?" in value or "#" in value:
        return HIDDEN
    return value


def _shown_helper(value: object) -> str:
    if not isinstance(value, str) or _CONTROL.search(value) or _KEY_LIKE.search(value):
        return HIDDEN
    if any(secret in value for secret in _secret_values()):
        return HIDDEN
    return value


# --- Reading ----------------------------------------------------------------------------------


@dataclass
class Current:
    path: Path
    exists: bool
    raw: bytes
    data: dict[str, Any]
    bom: bool
    newline: str
    final_newline: bool

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SetupError("the settings file has the same key twice in an object; fix it first")
        result[key] = value
    return result


def _no_constant(_: str) -> Any:
    raise SetupError("the settings file is not valid JSON (NaN or Infinity); fix it first")


def parse(raw: bytes) -> tuple[dict[str, Any], bool]:
    """The top-level object and whether there was a UTF-8 BOM. Refuses anything odd."""
    bom = raw.startswith(_UTF8_BOM)
    try:
        text = raw[len(_UTF8_BOM) :].decode("utf-8") if bom else raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SetupError("the settings file is not UTF-8 text; fix it first") from None
    if not text.strip():
        return {}, bom  # an empty file is an empty object
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except (ValueError, RecursionError):  # JSONDecodeError is a ValueError
        raise SetupError("the settings file is not valid JSON; fix it first") from None
    if not isinstance(data, dict):
        raise SetupError("the settings file is not a JSON object; fix it first")
    if "env" in data and not isinstance(data["env"], dict):
        raise SetupError('"env" in the settings file is not an object; fix it first')
    return data, bom


def read_current(path: Path) -> Current:
    if os.path.islink(path):
        raise SetupError("the settings file is a link; edit the file it points to by hand")
    if not os.path.lexists(path):
        return Current(path, False, b"", {}, False, "\n", True)
    if not path.is_file():
        raise SetupError("the settings path exists and is not a file; not touching it")
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_SETTINGS_BYTES + 1)
    except OSError:
        raise SetupError("cannot read the settings file; nothing was changed") from None
    if len(raw) > MAX_SETTINGS_BYTES:
        raise SetupError("the settings file is larger than 1 MiB; not touching it")
    data, bom = parse(raw)
    newline = "\r\n" if b"\r\n" in raw else "\n"
    final = raw.endswith(b"\n") or not raw.strip(_UTF8_BOM + b" \t\r\n")
    return Current(path, True, raw, data, bom, newline, final)


# --- Changes ----------------------------------------------------------------------------------


def apply_change(data: dict[str, Any], url: str, helper: str | None) -> dict[str, Any]:
    """`data` with our entries set. Key order is kept; new keys go last."""
    new = copy.deepcopy(data)
    env = new.setdefault("env", {})
    env[BASE_URL] = url
    if helper is not None:
        new[HELPER] = helper
    return new


def uninstall_change(data: dict[str, Any], url: str, helper: str | None) -> dict[str, Any]:
    """`data` without our entries; SetupError (code 2) if one of them was changed by someone."""
    new = copy.deepcopy(data)
    env = new.get("env")
    if isinstance(env, dict) and BASE_URL in env:
        if env[BASE_URL] != url:
            raise SetupError(
                f"env.{BASE_URL} is not the value this command would set (--url); not removing it"
            )
        del env[BASE_URL]
        if not env:
            del new["env"]
    if helper is not None and HELPER in new:
        if new[HELPER] != helper:
            raise SetupError(f"{HELPER} is not the command given; not removing it")
        del new[HELPER]
    return new


def render(data: dict[str, Any], current: Current) -> bytes:
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if current.final_newline:
        text += "\n"
    if current.newline != "\n":
        text = text.replace("\n", current.newline)
    return (_UTF8_BOM if current.bom else b"") + text.encode("utf-8")


def _redact(value: Any, path: tuple[str, ...]) -> Any:
    """Every value hidden except the two this command owns (and those only if harmless)."""
    if path == ("env", BASE_URL):
        return _shown_url(value)
    if path == (HELPER,):
        return _shown_helper(value)
    if isinstance(value, dict):
        return {key: _redact(item, (*path, key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, (*path, "[]")) for item in value]
    if value is None or isinstance(value, bool):
        return value
    return HIDDEN


def safe_diff(old: dict[str, Any], new: dict[str, Any], name: str) -> str:
    """A unified diff of the two documents with every value we do not own hidden."""

    def lines(data: dict[str, Any]) -> list[str]:
        if not data:
            return []
        return (json.dumps(_redact(data, ()), indent=2, ensure_ascii=False) + "\n").splitlines(
            keepends=True
        )

    return "".join(difflib.unified_diff(lines(old), lines(new), f"{name} (now)", f"{name} (after)"))


# --- Target -----------------------------------------------------------------------------------


def user_settings_path() -> Path:
    """$CLAUDE_CONFIG_DIR/settings.json, otherwise ~/.claude/settings.json (USERPROFILE on
    Windows, HOME elsewhere: Path.home())."""
    folder = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    base = Path(folder).expanduser() if folder else Path.home() / ".claude"
    return base / SETTINGS


def _is_shared_project_file(path: Path) -> bool:
    """A `.claude/settings.json` at the root of a repository (committed and shared)."""
    folder = path.parent
    return (
        path.name == SETTINGS
        and folder.name == ".claude"
        and os.path.lexists(folder.parent / ".git")
    )


def target_path(project: bool) -> Path:
    if project:
        return Path.cwd() / ".claude" / LOCAL_SETTINGS
    path = user_settings_path()
    cwd = Path.cwd().resolve()
    same_as_project = path.parent.resolve() == cwd / ".claude" and cwd != Path.home().resolve()
    if same_as_project or _is_shared_project_file(path):
        raise SetupError(
            "that settings file is a repository's shared .claude/settings.json (committed and "
            "shared); use --project for .claude/settings.local.json"
        )
    return path


# --- Writing ----------------------------------------------------------------------------------


def write_settings(current: Current, content: bytes) -> Path | None:
    """Replace the settings file atomically if it has not changed since it was read. Returns
    the backup, if there was a file to back up."""
    path = current.path
    folder = path.parent
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        raise SetupError("cannot create the settings folder; nothing was changed", 1) from None
    temp = folder / f"{path.name}.tmp-{secrets.token_hex(8)}"
    backup: Path | None = None
    try:
        init._write_all(init._open_new(temp), content)
        parse(temp.read_bytes())  # what we wrote reads back
        if current.exists:
            if os.path.islink(path) or not path.is_file():
                raise SetupError("the settings file changed meanwhile; run again", 1)
            now = path.read_bytes()
            if hashlib.sha256(now).hexdigest() != current.digest:
                raise SetupError("the settings file changed meanwhile; run again", 1)
            backup = init.write_backup(path, now, f"{path.name}.bak-")
        elif os.path.lexists(path):
            raise SetupError("the settings file appeared meanwhile; run again", 1)
        init._replace(temp, path)
    except OSError:
        if backup is None:
            message = "could not write the settings file; nothing was changed"
        else:
            message = (
                f"could not write the settings file; it was not replaced and a copy is in "
                f"{backup.name}"
            )
        raise SetupError(message, 1) from None
    finally:
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok=True)
    init._sync_folder(folder)
    return backup


# --- The command ------------------------------------------------------------------------------


def _warnings(current: Current, new: dict[str, Any], uninstall: bool) -> list[str]:
    notes = []
    env = current.data.get("env")
    if isinstance(env, dict):
        for name in CREDENTIAL_VARIABLES:
            if name in env:
                notes.append(
                    f"env.{name} is set in this file (its value is not shown). This file wins "
                    "over your shell: if it is a real Anthropic key, Antifaz refuses it with "
                    "401. Remove it and put your Antifaz key in ANTHROPIC_AUTH_TOKEN, outside "
                    "this file"
                )
        old_url = env.get(BASE_URL)
        new_env = new.get("env")
        new_url = new_env.get(BASE_URL) if isinstance(new_env, dict) else None
        if not uninstall and old_url is not None and old_url != new_url:
            notes.append(
                f"env.{BASE_URL} already points somewhere else; it will be replaced (see the "
                "diff; the backup keeps the old file)"
            )
    if not uninstall and HELPER in current.data and current.data[HELPER] != new.get(HELPER):
        notes.append(f"{HELPER} is already set; it will be replaced (see the diff)")
    if uninstall and HELPER in new:
        notes.append(
            f"{HELPER} is set and stays; to remove it, pass --key-helper with the same command"
        )
    if os.environ.get("ANTHROPIC_API_KEY"):
        notes.append(
            "ANTHROPIC_API_KEY is set in this shell (value not shown): if it is a real "
            "Anthropic key and Claude Code uses it, Antifaz refuses it with 401; unset it"
        )
    managed = _managed_folder()
    if managed is not None and (managed / "managed-settings.json").exists():
        notes.append(
            f"a managed settings file exists in {managed}: your organization's values win over "
            "this file"
        )
    return notes


def _confirm(args: argparse.Namespace) -> None:
    if args.yes:
        return
    if not _stdin_is_terminal():
        raise SetupError("no terminal to confirm; add --yes to write without asking")
    if _ask("Type yes to write this change (a backup is kept): ").strip() != "yes":
        raise SetupError("nothing was changed")


def _next_steps(url: str, args: argparse.Namespace, helper: str | None) -> str:
    if args.uninstall:
        return "\n".join(
            [
                "Next steps:",
                "  restart Claude Code: it talks to Anthropic directly again",
                "  remove ANTHROPIC_AUTH_TOKEN if it holds your Antifaz key",
            ]
        )
    key_line = (
        "  1. your apiKeyHelper prints the Antifaz key; nothing else to set"
        if helper is not None
        else "  1. put your Antifaz key (ANTIFAZ_API_KEY in Antifaz's .env) in "
        "ANTHROPIC_AUTH_TOKEN, never in a settings file:"
    )
    lines = ["Next steps:", key_line]
    if helper is None:
        lines += [
            "       bash/zsh (in ~/.bashrc or ~/.zshrc):  export ANTHROPIC_AUTH_TOKEN=<your key>",
            '       PowerShell:  [Environment]::SetEnvironmentVariable("ANTHROPIC_AUTH_TOKEN", '
            '"<your key>", "User")',
            "       (a key typed at a prompt stays in the shell history: add it to the profile "
            "file with an editor)",
        ]
    lines += [
        "  2. restart Claude Code; /status should show the Anthropic base URL",
        f"  3. antifaz doctor --url {url}",
        "If Antifaz is down, Claude Code shows a connection error and sends nothing to the "
        "model provider.",
        "To switch it off: antifaz setup claude-code --uninstall --apply"
        + (" --project" if args.project else "")
        + (f" --url {url}" if url != DEFAULT_URL else "")
        + (" --key-helper <the same command>" if helper is not None else ""),
    ]
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    try:
        url = checked_url(args.url)
        helper = checked_helper(args.key_helper) if args.key_helper is not None else None
        if args.yes and not args.apply:
            raise SetupError("--yes only goes with --apply")
        path = target_path(args.project)
        current = read_current(path)
        if args.uninstall:
            new = uninstall_change(current.data, url, helper)
        else:
            new = apply_change(current.data, url, helper)
        if not args.uninstall and urlsplit(url).scheme == "http" and not _is_loopback(url):
            _warn(
                "warning: --url is plain http to another machine: the Antifaz key would travel "
                "unencrypted; use https"
            )
        for note in _warnings(current, new, args.uninstall):
            _warn(f"warning: {note}")
        if helper is not None and not args.uninstall:
            _warn(
                f"note: Claude Code runs the {HELPER} command through your shell each time it "
                "needs a key; check that it prints only the key"
            )
        if new == current.data:
            _say(f"antifaz setup claude-code: {path} already as wanted; nothing to do")
            return 0
        diff = safe_diff(current.data, new, path.name)
        state = f"SHA-256 {current.digest}" if current.exists else "does not exist yet"
        _say(f"antifaz setup claude-code: {path} ({state})")
        _say(diff.rstrip("\n"))
        _say("(values this command does not own are shown as <hidden>)")
        if current.exists and render(current.data, current) != current.raw:
            _say("note: the whole file is rewritten with 2-space indentation")
        if not args.apply:
            _say("Dry run: nothing was written. Add --apply to write it.")
            return 0
        _confirm(args)
        backup = write_settings(current, render(new, current))
    except SetupError as error:
        _warn(str(error))
        return error.code
    except (EOFError, KeyboardInterrupt):
        _warn("cancelled; nothing was changed")
        return 2
    _say(f"antifaz setup claude-code: wrote {path}")
    if backup is not None:
        _say(f"  the previous file is kept as {backup.name}")
    _say(_next_steps(url, args, helper))
    return 0
