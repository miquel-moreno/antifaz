"""`antifaz setup claude-code`: point Claude Code at Antifaz, never writing a key (issue 31).

ADR-0017. What it changes, and nothing else:

- `env.ANTHROPIC_BASE_URL` (an http(s) URL without user, password, query or fragment), and
- `apiKeyHelper`, only when the user passes --key-helper (a command Claude Code runs to get
  the key; refused if it looks like it holds a key itself).

The key goes in the ANTHROPIC_AUTH_TOKEN environment variable (sent as `Authorization:
Bearer`), never in a settings file: this command never reads it and never writes it.

Target: the user settings file (`$CLAUDE_CONFIG_DIR/settings.json`, otherwise
`~/.claude/settings.json`; `%USERPROFILE%\\.claude` on Windows) or, with --project,
`.claude/settings.local.json` in the current folder. A user target inside a git work tree
(a `.git` in the folder or any folder above it, after resolving links) is refused: that covers
a repository's shared `.claude/settings.json`, which is committed and shared.

Default is a dry run: it prints a diff and the SHA-256 of the file, and writes nothing. In the
diff every value is hidden except the NEW values of the two entries this command owns; their
previous values are shown only as a short SHA-256. With --apply, after typing `yes` (or
--yes): the file is read again and must still have the same SHA-256 ("changed meanwhile; run
again" otherwise), a dated backup of the whole previous file is written (O_EXCL, 0600, never
over another one; next to the user file, or for --project under
`$CLAUDE_CONFIG_DIR/antifaz-backups/<hash of the project folder>/`, never inside a git work
tree), the new file goes to a 0600 temp file in the same folder, is parsed back, and replaces
the old one with `os.replace` (retried on Windows). A UTF-8 BOM and CRLF line ends are kept;
the JSON is written with 2-space indent. Anything unreadable (not UTF-8, invalid JSON, a key
twice in an object, NaN or a number too large for a float, lone surrogates, nesting deeper
than 64, a top level or an `env` that is not an object, a symlink or a hard link) aborts
without touching anything.

Ownership without comments or a sidecar file: --uninstall removes `env.ANTHROPIC_BASE_URL` only
if it still equals --url, and `apiKeyHelper` only if --key-helper is given and still equal;
otherwise it aborts with a message naming the entry and changes nothing. An `env` left empty
is removed.

Exit codes: 0 done (or nothing to do, or a dry run); 2 usage or refused (nothing written);
1 the file changed meanwhile or could not be written (the old file is untouched).

Forbidden: reading or writing any key. Printing a value this command does not own.
"""

import argparse
import contextlib
import copy
import difflib
import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
import sys
from collections import Counter
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
MAX_DEPTH = 64
HIDDEN = "<hidden>"
BACKUPS = "antifaz-backups"
_UTF8_BOM = b"\xef\xbb\xbf"
# What a key looks like (a helper command or a URL path should never hold one): a long hex run,
# a provider-style key, or a long token with mixed case or digits and high entropy. Readable
# names ("get-antifaz-key-from-the-vault") and host names are not keys.
_HEX_RUN = re.compile(r"[0-9a-fA-F]{32,}")
_PROVIDER_KEY = re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}")
_TOKEN = re.compile(r"[A-Za-z0-9_\-+=]{24,}")
_MIN_ENTROPY = 4.0  # bits per character; random base64 is close to 6, English words below 4.2
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MISSING = object()
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
    # Host labels may be long or hex-like (load balancers); only the path is checked for keys.
    if looks_like_key(urlsplit(url).path) or any(secret in url for secret in _secret_values()):
        raise SetupError("--url seems to contain a key; the settings file must never hold one")
    return url


def _entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def looks_like_key(text: str) -> bool:
    if _HEX_RUN.search(text) or _PROVIDER_KEY.search(text):
        return True
    for token in _TOKEN.findall(text):
        mixed = any(c.isdigit() for c in token) or (token.lower() != token != token.upper())
        if mixed and _entropy(token) >= _MIN_ENTROPY:
            return True
    return False


def _is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:  # a name, not an address: 127.evil.example is not loopback
        return False


def _secret_values() -> list[str]:
    return [
        value for name in _SECRET_VARIABLES if len(value := os.environ.get(name, "").strip()) >= 8
    ]


def checked_helper(value: str) -> str:
    """The helper command as given, or SetupError if it is empty, odd or seems to hold a key."""
    if not value.strip() or len(value) > MAX_HELPER or _CONTROL.search(value):
        raise SetupError(f"--key-helper expects one command line (at most {MAX_HELPER} characters)")
    if looks_like_key(value) or any(secret in value for secret in _secret_values()):
        raise SetupError(
            "--key-helper seems to contain a key; give a command that reads the key from a "
            "file or a vault instead (the settings file must never hold a key)"
        )
    return value


def _fingerprint(value: object) -> str:
    """How a previous value of ours is shown: never the value, only a short SHA-256."""
    digest = hashlib.sha256(json.dumps(value, ensure_ascii=True).encode()).hexdigest()
    return f"<previous value, sha256 {digest[:8]}>"


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


def _finite(text: str) -> float:
    number = float(text)
    if not math.isfinite(number):  # 1e999 parses as infinity, which JSON cannot hold
        raise SetupError("the settings file has a number too large to keep; fix it first")
    return number


def _check_shape(data: Any) -> None:
    """Nesting at most MAX_DEPTH, and every key and string writable as UTF-8 (no lone
    surrogates such as an escaped \\ud800). Iterative: no recursion on hostile input."""
    stack: list[tuple[Any, int]] = [(data, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise SetupError(f"the settings file is nested deeper than {MAX_DEPTH}; fix it first")
        items: list[Any] = []
        if isinstance(node, dict):
            items = list(node.values())
            texts = list(node.keys())
        elif isinstance(node, list):
            items, texts = node, []
        else:
            texts = [node] if isinstance(node, str) else []
        for text in texts:
            try:
                text.encode("utf-8")
            except UnicodeEncodeError:
                raise SetupError(
                    "the settings file has text that is not valid Unicode; fix it first"
                ) from None
        stack.extend((item, depth + 1) for item in items)


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
        data = json.loads(
            text,
            object_pairs_hook=_no_duplicates,
            parse_constant=_no_constant,
            parse_float=_finite,
        )
    except (ValueError, RecursionError):  # JSONDecodeError is a ValueError
        raise SetupError("the settings file is not valid JSON; fix it first") from None
    if not isinstance(data, dict):
        raise SetupError("the settings file is not a JSON object; fix it first")
    _check_shape(data)
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
    if path.stat().st_nlink > 1:
        raise SetupError(
            "the settings file has other hard links; replacing it would split them, so edit it "
            "by hand"
        )
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
    try:
        text = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)
        if current.final_newline:
            text += "\n"
        if current.newline != "\n":
            text = text.replace("\n", current.newline)
        return (_UTF8_BOM if current.bom else b"") + text.encode("utf-8")
    except (ValueError, RecursionError):  # UnicodeEncodeError is a ValueError; parse() checks
        raise SetupError("the settings file cannot be written back as JSON; fix it first") from None


OWNED = (("env", BASE_URL), (HELPER,))


def _owned_value(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = data
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return _MISSING
        node = node[key]
    return node


def _redact(value: Any, path: tuple[str, ...], old: dict[str, Any] | None) -> Any:
    """Every value hidden. Ours: the previous value (old is None) only as a fingerprint; the
    new one (old given) in clear, unless it is the same as before."""
    if path in OWNED:
        if old is None or _owned_value(old, path) == value:
            return _fingerprint(value)
        return value
    if isinstance(value, dict):
        return {key: _redact(item, (*path, key), old) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, (*path, "[]"), old) for item in value]
    if value is None or isinstance(value, bool):
        return value
    return HIDDEN


def safe_diff(old: dict[str, Any], new: dict[str, Any], name: str) -> str:
    """A unified diff of the two documents in which only our new values can be read."""

    def lines(data: dict[str, Any], previous: dict[str, Any] | None) -> list[str]:
        if not data:
            return []
        shown = _redact(data, (), previous)
        return (json.dumps(shown, indent=2, ensure_ascii=False) + "\n").splitlines(keepends=True)

    return "".join(
        difflib.unified_diff(lines(old, None), lines(new, old), f"{name} (now)", f"{name} (after)")
    )


# --- Target -----------------------------------------------------------------------------------


def user_settings_path() -> Path:
    """$CLAUDE_CONFIG_DIR/settings.json, otherwise ~/.claude/settings.json (USERPROFILE on
    Windows, HOME elsewhere: Path.home())."""
    folder = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    base = Path(folder).expanduser() if folder else Path.home() / ".claude"
    return base / SETTINGS


def _real(path: Path) -> str:
    """The path with links and junctions resolved, as the OS compares it (case on Windows;
    abspath also drops Windows' trailing dots and spaces)."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def git_work_tree(path: Path) -> Path | None:
    """The folder with a `.git` (folder or file) at or above `path`, links resolved."""
    real = Path(os.path.realpath(os.path.abspath(path)))
    for folder in (real, *real.parents):
        if os.path.lexists(folder / ".git"):
            return folder
    return None


def target_path(project: bool) -> Path:
    if project:
        # settings.local.json is the per-person file; Claude Code git-ignores it when it saves
        # there. It may live in a repository (it is the point of --project).
        return Path.cwd() / ".claude" / LOCAL_SETTINGS
    path = user_settings_path()
    cwd = Path.cwd()
    project_folder = _real(cwd / ".claude")
    same_as_project = _real(path.parent) == project_folder and _real(cwd) != _real(Path.home())
    if same_as_project or git_work_tree(path.parent) is not None:
        raise SetupError(
            "that settings file is inside a git work tree, where it could be committed and "
            "shared (like a repository's shared .claude/settings.json); point CLAUDE_CONFIG_DIR "
            "elsewhere, or use --project for .claude/settings.local.json"
        )
    return path


def backup_folder(target: Path, project: bool) -> Path:
    """Where the copy of the previous file goes. Never inside a git work tree: the copy holds
    the whole file, secrets of other tools included."""
    if project:
        digest = hashlib.sha256(_real(target.parent.parent).encode("utf-8")).hexdigest()[:16]
        folder = user_settings_path().parent / BACKUPS / digest
    else:
        folder = target.parent
    if git_work_tree(folder) is not None:
        raise SetupError(
            "the backup would go inside a git work tree; point CLAUDE_CONFIG_DIR at a folder "
            "outside any repository"
        )
    return folder


def _linked(path: Path) -> bool:
    """Whether the folder of `path` or any folder above it is a symlink or a junction."""
    folder = Path(os.path.abspath(path.parent))
    return any(os.path.islink(p) or os.path.isjunction(p) for p in (folder, *folder.parents))


# --- Writing ----------------------------------------------------------------------------------


def write_settings(current: Current, content: bytes, backups: Path) -> Path | None:
    """Replace the settings file atomically if it has not changed since it was read. Returns
    the backup (in `backups`), if there was a file to back up."""
    path = current.path
    folder = path.parent
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        if current.exists:
            backups.mkdir(mode=0o700, parents=True, exist_ok=True)
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
            backup = init.write_backup(backups / path.name, now, f"{path.name}.bak-")
        elif os.path.lexists(path):
            raise SetupError("the settings file appeared meanwhile; run again", 1)
        init._replace(temp, path)
    except OSError:
        if backup is None:
            message = "could not write the settings file; nothing was changed"
        else:
            message = (
                f"could not write the settings file; it was not replaced and a copy is in {backup}"
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


def _outranking_notes(target: Path, project: bool, url: str) -> list[str]:
    """Project files in the current folder that set ANTHROPIC_BASE_URL above the target (names
    only): there, Claude Code would not use Antifaz. settings.local.json is the top project
    file, so nothing outranks it but managed settings and --settings."""
    if project:
        return []
    notes = []
    for name in (LOCAL_SETTINGS, SETTINGS):
        other = Path.cwd() / ".claude" / name
        if not other.is_file() or _real(other) == _real(target):
            continue
        try:
            data = read_current(other).data
        except (SetupError, OSError):
            notes.append(f"could not read {other} to check whether it overrides this setting")
            continue
        value = _owned_value(data, ("env", BASE_URL))
        if value is not _MISSING and value != url:
            notes.append(
                f"{other} sets env.{BASE_URL} (value not shown) and outranks {target.name}: "
                "in this folder Claude Code would not use Antifaz"
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
        if _linked(path):
            # Work on the real folder: the temp file and os.replace then stay in one folder
            # (O_EXCL through a Windows junction fails) and the link itself is left alone.
            path = Path(os.path.realpath(path))
            _warn(
                "warning: the settings folder (or a folder above it) is a link or junction: the "
                f"file really is {path}, and that is the file changed"
            )
        backups = backup_folder(path, args.project)
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
        notes = _warnings(current, new, args.uninstall)
        if not args.uninstall:
            notes += _outranking_notes(path, args.project, url)
        for note in notes:
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
        _say("(other values are shown as <hidden>; previous values of ours only as a SHA-256)")
        if current.exists and render(current.data, current) != current.raw:
            _say("note: the whole file is rewritten with 2-space indentation")
        if not args.apply:
            _say("Dry run: nothing was written. Add --apply to write it.")
            return 0
        _confirm(args)
        backup = write_settings(current, render(new, current), backups)
    except SetupError as error:
        _warn(str(error))
        return error.code
    except (EOFError, KeyboardInterrupt):
        _warn("cancelled; nothing was changed")
        return 2
    _say(f"antifaz setup claude-code: wrote {path}")
    if backup is not None:
        _say(f"  backup of the previous file: {backup}")
        _say(
            "  it is a copy of the whole previous file, including any secrets in it (tokens in "
            "permission rules, env values); delete it when you no longer need it (one copy is "
            "kept per change: they add up)"
        )
        if sys.platform == "win32":
            _say(
                "  on Windows 0600 does not apply: the copy has the permissions of its folder "
                "(in your profile, only you)"
            )
    _say(_next_steps(url, args, helper))
    return 0
