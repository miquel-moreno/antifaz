"""`antifaz init`: write a .env that starts, without ever showing a provider key (ADR-0017).

- ANTIFAZ_API_KEY is made here (`secrets.token_hex(32)`, drawn again in the very unlikely case
  it fails the startup check). It is shown once, only on a terminal or with --show-key.
- Provider keys never come from argv: hidden input (getpass) when interactive; with
  --non-interactive, from an environment variable named by --*-key-env or from stdin
  (--*-key-stdin, one stdin source at most). Spaces, quotes and "Bearer " are trimmed.
- An existing .env is only replaced after typing "yes" (or --force with --non-interactive),
  and a dated backup is kept first (O_EXCL: it never overwrites another one).
- The new file is written to a 0600 temp file (O_EXCL) in the same folder, checked with
  `check_safe_to_start` reading THAT file alone (never the process environment), and only
  then moved over .env with `os.replace`. On any failure the temp file goes and .env stays.
- No network. Messages name variables, never values.

Exit codes: 0 written; 2 usage, refused or a bad value (nothing written); 1 the file could not
be written or checked (the old .env, if any, is untouched).

Forbidden: reading a key from argv. Printing a provider key. Logging anything.
"""

import argparse
import contextlib
import getpass
import os
import re
import secrets
import sys
import time
import warnings
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from antifaz.cli import env_template
from antifaz.config import (
    Settings,
    UnsafeConfigError,
    _format_problem,
    _gateway_key_problem,
    _host_problem,
    check_safe_to_start,
)

ENV_NAME = ".env"
BACKUP_PREFIX = ".env.bak-"
# What may be written unquoted for both pydantic-settings and `docker --env-file`: no quotes,
# no "#", no "$", no spaces, no backslash. Real provider keys use far fewer characters.
_SAFE_VALUE = re.compile(r"[A-Za-z0-9._~+/=:-]+")
_SAFE_HOST = re.compile(r"[A-Za-z0-9.*:\[\]-]+")
_VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BEARER = re.compile(r"bearer\s+", re.IGNORECASE)
_KEY_TRIES = 100
_REPLACE_TRIES = 5
_BACKUP_TRIES = 1000
# Longest key read from stdin (real provider keys are well under 300 characters).
MAX_STDIN_KEY = 4096
_BOM = "﻿"

PROVIDERS = (
    # (name shown, variable written, usual prefix, option stem)
    ("OpenAI", env_template.OPENAI_KEY, "sk-", "openai"),
    ("Anthropic", env_template.ANTHROPIC_KEY, "sk-ant-", "anthropic"),
)


class InitError(Exception):
    """A refusal with a message safe to print (names, never values) and its exit code."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _say(message: str) -> None:
    print(message)


def _warn(message: str) -> None:
    print(f"antifaz init: {message}", file=sys.stderr)


# --- Arguments --------------------------------------------------------------------------------


def _variable_name(value: str) -> str:
    """--*-key-env takes the NAME of a variable; anything else (a pasted key) is refused."""
    if not _VARIABLE_NAME.fullmatch(value):
        # Never repeat the value: it could be the key itself.
        raise argparse.ArgumentTypeError("expects the name of an environment variable")
    return value


def add_parser(commands: "argparse._SubParsersAction[Any]") -> None:
    command = commands.add_parser(
        "init",
        help="write a .env with a new Antifaz key; provider keys are asked hidden, never "
        "taken from the command line",
    )
    command.add_argument(
        "--path", default=".", help="folder where .env is written (default: current folder)"
    )
    command.add_argument(
        "--non-interactive",
        action="store_true",
        help="ask nothing (CI, scripts); provider keys from --*-key-env or --*-key-stdin",
    )
    for _, variable, _, stem in PROVIDERS:
        command.add_argument(
            f"--{stem}-key-env",
            type=_variable_name,
            metavar="VAR",
            help=f"read {variable} from the environment variable VAR (its name, not the key)",
        )
        command.add_argument(
            f"--{stem}-key-stdin",
            action="store_true",
            help=f"read {variable} from stdin (only one key can come from stdin)",
        )
    command.add_argument(
        "--allowed-hosts",
        metavar="HOSTS",
        help="ANTIFAZ_ALLOWED_HOSTS, comma-separated "
        f"(default: {env_template.DEFAULT_ALLOWED_HOSTS})",
    )
    command.add_argument(
        "--force",
        action="store_true",
        help="replace an existing .env without asking (a backup is always kept)",
    )
    command.add_argument(
        "--show-key",
        action="store_true",
        help="print the new Antifaz key even when the output is not a terminal",
    )


# --- Keys --------------------------------------------------------------------------------------


def generate_gateway_key(token_hex: Callable[[int], str] = secrets.token_hex) -> str:
    """64 random hex characters that pass the startup check (drawn again if not)."""
    for _ in range(_KEY_TRIES):
        key = token_hex(32)
        if _gateway_key_problem(key) is None:
            return key
    raise InitError("could not make a random key; try again", code=1)


def clean_provider_key(raw: str) -> str:
    """Trim what a paste brings along: spaces, line ends, quotes and a "Bearer " prefix."""
    key = raw.strip().lstrip(_BOM).strip()  # a BOM from a file saved by Notepad
    for _ in range(2):  # '"Bearer sk-..."' and 'Bearer "sk-..."'
        if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
            key = key[1:-1].strip()
        key = _BEARER.sub("", key, count=1) if _BEARER.match(key) else key
        key = key.strip()
    return key


def _checked_provider_key(name: str, variable: str, prefix: str, raw: str) -> str | None:
    """The cleaned key, or None when empty. Refuses (by variable name) a key that cannot work."""
    key = clean_provider_key(raw)
    if not key:
        return None
    problem = _format_problem(key)
    if problem:
        raise InitError(f"{variable} {problem}")
    if not _SAFE_VALUE.fullmatch(key):
        raise InitError(
            f"{variable} has characters that cannot be written safely to .env "
            "(quotes, #, $, backslash or spaces); check that the whole key was pasted"
        )
    if not key.startswith(prefix):
        _warn(
            f"warning: the {name} key does not start with {prefix!r} as usual; written anyway "
            "(fine for a compatible provider or a proxy)"
        )
    return key


def _read_stdin_key(variable: str) -> str:
    try:
        raw = sys.stdin.read(MAX_STDIN_KEY + 1)
    except (OSError, UnicodeDecodeError):
        raise InitError(f"cannot read {variable} from stdin") from None
    if len(raw) > MAX_STDIN_KEY:
        raise InitError(f"{variable} from stdin is longer than {MAX_STDIN_KEY} characters")
    return raw


def _ask_hidden(prompt: str) -> str:
    """getpass, but never its fallback that shows what is typed (GetPassWarning)."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except getpass.GetPassWarning:
            raise InitError(
                "this terminal cannot read a key hidden; use --non-interactive with "
                "--openai-key-env / --anthropic-key-env"
            ) from None


def _provider_keys(args: argparse.Namespace, interactive: bool) -> dict[str, str | None]:
    keys: dict[str, str | None] = {}
    for name, variable, prefix, stem in PROVIDERS:
        env_name: str | None = getattr(args, f"{stem}_key_env")
        from_stdin: bool = getattr(args, f"{stem}_key_stdin")
        if env_name is not None:
            raw = os.environ.get(env_name)
            if raw is None:
                # Never the name itself: a key pasted as VAR can look like one (gsk_...).
                raise InitError(f"the variable named by --{stem}-key-env is not set")
        elif from_stdin:
            raw = _read_stdin_key(variable)
        elif interactive:
            raw = _ask_hidden(f"{name} key for {variable} (hidden; Enter to skip this provider): ")
        else:
            raw = ""
        keys[variable] = _checked_provider_key(name, variable, prefix, raw)
    return keys


def _check_sources(args: argparse.Namespace, interactive: bool) -> None:
    stdin_sources = 0
    for _, _, _, stem in PROVIDERS:
        from_env = getattr(args, f"{stem}_key_env") is not None
        from_stdin = getattr(args, f"{stem}_key_stdin")
        if from_env and from_stdin:
            raise InitError(f"use either --{stem}-key-env or --{stem}-key-stdin, not both")
        stdin_sources += from_stdin
    if stdin_sources > 1:
        raise InitError("only one key can come from stdin; use --*-key-env for the other")
    if stdin_sources and interactive:
        raise InitError("--*-key-stdin needs --non-interactive")
    if stdin_sources and sys.stdin.isatty():
        raise InitError(
            "--*-key-stdin reads a key sent through a pipe: pipe the key, or use interactive "
            "mode (without --non-interactive) to type it hidden"
        )


def _allowed_hosts(value: str | None) -> str:
    if value is None:
        return env_template.DEFAULT_ALLOWED_HOSTS
    hosts = [host.strip() for host in value.split(",") if host.strip()]
    if not hosts or not all(_SAFE_HOST.fullmatch(host) for host in hosts):
        raise InitError(
            "--allowed-hosts expects host names separated by commas (letters, digits, '.', "
            "'-', '*', ':' and brackets for IPv6)"
        )
    if any(_host_problem(host) for host in hosts):
        raise InitError(
            "--allowed-hosts: '*' is not accepted and a wildcard needs two labels after it "
            "('*.example.com')"
        )
    return ",".join(hosts)


# --- The file ----------------------------------------------------------------------------------


def render(values: dict[str, str | None]) -> str:
    """The template with `values` filled in; a variable set to None loses its line."""
    lines = []
    for line in env_template.ENV_TEMPLATE.splitlines():
        name, sep, _ = line.partition("=")
        if sep and not line.startswith("#") and name in values:
            value = values[name]
            if value is None:
                continue
            line = f"{name}={value}"
        lines.append(line)
    return "\n".join(lines) + "\n"


class _FileOnlySettings(Settings):
    """Settings read from the .env file given and nothing else (no process environment)."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (dotenv_settings,)


def settings_from_file(path: Path) -> Settings:
    """What the gateway would read from `path` alone (the process environment is ignored)."""
    return _FileOnlySettings(_env_file=path)


def _open_new(path: Path) -> int:
    """A new file only the owner can read (O_EXCL: never an existing file or a link)."""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    if hasattr(os, "fchmod"):
        os.fchmod(fd, 0o600)  # whatever the umask
    return fd


def _write_all(fd: int, data: bytes) -> None:
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def write_backup(path: Path, data: bytes, prefix: str) -> Path:
    """Write `data` to <prefix>YYYYMMDD-HHMMSS (then -1, -2...) next to `path`, 0600 and O_EXCL:
    never over another file. Also used by `antifaz setup claude-code`."""
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    for attempt in range(_BACKUP_TRIES):
        name = f"{prefix}{stamp}" + (f"-{attempt}" if attempt else "")
        target = path.with_name(name)
        try:
            fd = _open_new(target)
        except FileExistsError:
            continue
        try:
            _write_all(fd, data)
        except OSError:
            target.unlink(missing_ok=True)
            raise
        return target
    raise OSError("no free backup name")


def _backup(env: Path) -> Path:
    """Copy `env` to .env.bak-YYYYMMDD-HHMMSS (then -1, -2...), never over another file."""
    return write_backup(env, env.read_bytes(), BACKUP_PREFIX)


def _replace(source: Path, target: Path) -> None:
    """os.replace, retried a little: on Windows an antivirus or editor may hold the file."""
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == _REPLACE_TRIES - 1:
                raise
            _sleep(0.1 * (attempt + 1))


def _sync_folder(folder: Path) -> None:
    if sys.platform == "win32":
        return
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_env(folder: Path, content: str, *, replace_existing: bool) -> Path | None:
    """Write `content` to folder/.env atomically, after checking it. Returns the backup.

    The temp file is removed whatever happens (also Ctrl-C). Messages never hold the path
    (it came from --path) nor any value.
    """
    env = folder / ENV_NAME
    temp = folder / f"{ENV_NAME}.tmp-{secrets.token_hex(8)}"
    backup: Path | None = None
    try:
        _write_all(_open_new(temp), content.encode("utf-8"))
        try:
            check_safe_to_start(settings_from_file(temp))
        except (UnsafeConfigError, ValidationError):
            # The messages name variables only, but this is init's own output: keep it fixed.
            raise InitError(
                "the new .env would not pass the startup check; nothing was changed", code=1
            ) from None
        if replace_existing:
            backup = _backup(env)
        elif os.path.lexists(env):  # appeared after the question: never replaced unasked
            raise InitError(".env appeared while init was running; nothing was changed")
        _replace(temp, env)
    except OSError:
        if backup is None:
            message = "could not write .env; nothing was changed"
        else:
            message = f"could not write .env; it was not replaced and a copy is in {backup.name}"
        raise InitError(message, code=1) from None
    finally:
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok=True)
    _sync_folder(folder)
    return backup


# --- The command ------------------------------------------------------------------------------


def _confirm_replace(env: Path, args: argparse.Namespace, interactive: bool) -> bool:
    """Whether an existing .env may be replaced. Raises when it may not."""
    if not os.path.lexists(env):
        return False
    if env.is_symlink() or not env.is_file():
        raise InitError(".env exists and is not a regular file; not touching it")
    if args.force:
        return True
    if not interactive:
        raise InitError(".env already exists; use --force to replace it (a backup is kept)")
    answer = input(
        ".env already exists in that folder. Type yes to replace it (a backup is kept): "
    )
    if answer.strip() != "yes":
        raise InitError("nothing was changed")
    return True


def _report(
    env: Path, key: str, backup: Path | None, keys: dict[str, str | None], show: bool
) -> None:
    lines = [f"antifaz init: wrote {env}"]
    if backup is not None:
        lines.append(f"  the previous file is kept as {backup.name}")
    for name, variable, _, _ in PROVIDERS:
        state = "set" if keys.get(variable) else "not set (its routes answer 503)"
        lines.append(f"  {name} key: {state}")
    if show:
        lines += [
            "",
            "Your Antifaz key (ANTIFAZ_API_KEY). It is shown only this once; it is also in .env.",
            "Clients send it as 'Authorization: Bearer <key>' or 'x-api-key: <key>'.",
            key,
            "",
        ]
    else:
        lines.append(
            "  the Antifaz key is in .env as ANTIFAZ_API_KEY; it is not shown because the "
            "output is not a terminal (use --show-key to print it)"
        )
    lines.append("  next: docker compose up -d")
    _say("\n".join(lines))


def run(args: argparse.Namespace) -> int:
    interactive = not args.non_interactive
    try:
        if interactive and not sys.stdin.isatty():
            raise InitError(
                "no terminal to ask the questions: run it with docker run -it, or use "
                "--non-interactive"
            )
        folder = Path(args.path)
        if not folder.is_dir():
            raise InitError("the folder given with --path does not exist")
        _check_sources(args, interactive)
        hosts = _allowed_hosts(args.allowed_hosts)
        env = folder / ENV_NAME
        replace_existing = _confirm_replace(env, args, interactive)
        keys = _provider_keys(args, interactive)
        key = generate_gateway_key()
        content = render({env_template.API_KEY: key, env_template.ALLOWED_HOSTS: hosts, **keys})
        backup = write_env(folder, content, replace_existing=replace_existing)
    except InitError as error:
        _warn(str(error))
        return error.code
    except (EOFError, KeyboardInterrupt):
        _warn("cancelled")
        return 2
    _report(env, key, backup, keys, show=args.show_key or sys.stdout.isatty())
    return 0
