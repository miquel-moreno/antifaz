"""`antifaz init` writes a .env that starts, without ever showing a provider key (issue 41).

Every test works in pytest's tmp_path: the real .env of the repository is never read or
written. Provider keys are canaries made at runtime (no key-like literal in the source).
"""

import argparse
import contextlib
import getpass
import io
import logging
import os
import re
import secrets
import socket
import stat
import sys
import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from dotenv import dotenv_values
from hypothesis import given, settings
from hypothesis import strategies as st

from antifaz.cli import _parser, main
from antifaz.cli import init as init_module
from antifaz.cli.env_template import ENV_TEMPLATE
from antifaz.config import Settings, UnsafeConfigError, _gateway_key_problem, check_safe_to_start

ROOT = Path(__file__).resolve().parents[2]
ENV_NAME = ".env"
BOM = chr(0xFEFF)
NL = chr(10)
VARIABLE = re.compile(r"^#?\s*(ANTIFAZ_[A-Z0-9_]+)=", re.MULTILINE)


def canary(prefix: str) -> str:
    """A fake provider key, different on every run, never a literal in the source."""
    return f"{prefix}canary{secrets.token_hex(20)}"


@pytest.fixture
def openai_key() -> str:
    return canary("sk-")


@pytest.fixture
def anthropic_key() -> str:
    return canary("sk-" + "ant-")


class _Terminal(io.StringIO):
    """A stdin (or stdout) that says it is a terminal."""

    def isatty(self) -> bool:
        return True


@pytest.fixture
def no_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))


@pytest.fixture
def tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", _Terminal(""))


@dataclass
class FakeGetpass:
    answers: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)


@pytest.fixture
def fake_getpass(monkeypatch: pytest.MonkeyPatch) -> FakeGetpass:
    """getpass answers from a queue and records each prompt."""
    fake = FakeGetpass()

    def getpass(prompt: str = "", stream: Any = None) -> str:
        fake.prompts.append(prompt)
        return fake.answers.pop(0)

    monkeypatch.setattr("getpass.getpass", getpass)
    return fake


def env_file(tmp_path: Path) -> Path:
    return tmp_path / ENV_NAME


def run(tmp_path: Path, *args: str) -> int:
    return main(["init", "--path", str(tmp_path), *args])


def output(capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err + caplog.text


def settings_of(path: Path) -> Settings:
    """The settings of `path` alone (no process environment), as init validates them."""
    return init_module.settings_from_file(path)


def leftovers(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in tmp_path.iterdir() if ".tmp" in p.name)


# --- The template ----------------------------------------------------------------------------


def test_template_has_the_same_variables_as_env_example() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert VARIABLE.findall(ENV_TEMPLATE) == VARIABLE.findall(example)


def test_template_holds_no_dollar_quote_or_inline_comment() -> None:
    for line in ENV_TEMPLATE.splitlines():
        assert "$" not in line
        if line and not line.startswith("#"):
            assert "#" not in line and '"' not in line and "'" not in line


# --- Non-interactive: the gateway key ---------------------------------------------------------


def test_non_interactive_writes_a_env_that_passes_the_startup_check(
    tmp_path: Path, no_tty: None
) -> None:
    assert run(tmp_path, "--non-interactive") == 0
    loaded = settings_of(env_file(tmp_path))
    check_safe_to_start(loaded)
    assert loaded.antifaz_api_key is not None
    assert len(loaded.antifaz_api_key.get_secret_value()) == 64
    assert loaded.openai_api_key is None and loaded.anthropic_api_key is None
    text = env_file(tmp_path).read_text(encoding="utf-8")
    assert "ANTIFAZ_OPENAI_API_KEY" not in text.replace("# ANTIFAZ_OPENAI_API_KEY", "")
    assert leftovers(tmp_path) == []


def test_the_written_file_parses_the_same_for_pydantic_settings_and_docker(
    tmp_path: Path, no_tty: None, monkeypatch: pytest.MonkeyPatch, openai_key: str
) -> None:
    monkeypatch.setenv("CI_OPENAI", openai_key)
    assert run(tmp_path, "--non-interactive", "--openai-key-env", "CI_OPENAI") == 0
    path = env_file(tmp_path)
    assert dotenv_values(path) == docker_env_file(path)
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            assert re.fullmatch(r"ANTIFAZ_[A-Z0-9_]+=[A-Za-z0-9._~+/=:,\[\]*-]*", line), line


def docker_env_file(path: Path) -> dict[str, str]:
    """`docker run --env-file`: VAR=VAL taken literally, # lines and blank lines skipped."""
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        name, _, value = stripped.partition("=")
        values[name] = value
    return values


def test_the_file_has_unix_line_endings_and_no_bom(tmp_path: Path, no_tty: None) -> None:
    assert run(tmp_path, "--non-interactive") == 0
    raw = env_file(tmp_path).read_bytes()
    assert b"\r" not in raw
    assert not raw.startswith(b"\xef\xbb\xbf")


@given(st.binary(min_size=32, max_size=32))
@settings(max_examples=300, deadline=None)
def test_generated_keys_always_pass_the_startup_check(first: bytes) -> None:
    """Whatever the first draw is, the key returned passes; a good first draw is kept."""
    draws = iter([first.hex()])

    def token_hex(size: int) -> str:
        assert size == 32
        return next(draws, None) or secrets.token_hex(size)

    key = init_module.generate_gateway_key(token_hex)
    assert _gateway_key_problem(key) is None
    if _gateway_key_problem(first.hex()) is None:
        assert key == first.hex()


@settings(max_examples=50, deadline=None)
@given(st.integers())
def test_real_generated_keys_pass_the_startup_check(_: int) -> None:
    key = init_module.generate_gateway_key()
    assert _gateway_key_problem(key) is None
    assert re.fullmatch(r"[0-9a-f]{64}", key)


def test_a_weak_random_key_is_drawn_again() -> None:
    draws = iter(["0" * 64, "ab" * 32, "0123456789abcdef" * 4])
    assert init_module.generate_gateway_key(lambda _: next(draws)) == "0123456789abcdef" * 4


# --- Non-interactive: provider keys -----------------------------------------------------------


def test_provider_keys_from_environment_variables_are_written_and_never_printed(
    tmp_path: Path,
    no_tty: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    openai_key: str,
    anthropic_key: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("CI_OPENAI", openai_key)
    monkeypatch.setenv("CI_ANTHROPIC", anthropic_key)
    code = run(
        tmp_path,
        "--non-interactive",
        "--openai-key-env",
        "CI_OPENAI",
        "--anthropic-key-env",
        "CI_ANTHROPIC",
    )
    assert code == 0
    loaded = settings_of(env_file(tmp_path))
    assert loaded.openai_api_key is not None
    assert loaded.openai_api_key.get_secret_value() == openai_key
    assert loaded.anthropic_api_key is not None
    assert loaded.anthropic_api_key.get_secret_value() == anthropic_key
    text = output(capsys, caplog)
    assert openai_key not in text and anthropic_key not in text
    assert "OpenAI" in text and "Anthropic" in text


def test_a_provider_key_can_come_from_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    anthropic_key: str,
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(f"  {anthropic_key}\r\n"))
    assert run(tmp_path, "--non-interactive", "--anthropic-key-stdin") == 0
    loaded = settings_of(env_file(tmp_path))
    assert loaded.anthropic_api_key is not None
    assert loaded.anthropic_api_key.get_secret_value() == anthropic_key
    assert anthropic_key not in output(capsys, caplog)


@pytest.mark.parametrize(
    "args",
    [
        ["--openai-key-stdin", "--anthropic-key-stdin"],
        ["--openai-key-stdin", "--openai-key-env", "CI_OPENAI"],
        ["--anthropic-key-stdin", "--anthropic-key-env", "CI_ANTHROPIC"],
    ],
)
def test_at_most_one_source_per_key_and_one_stdin_source(
    tmp_path: Path, no_tty: None, args: list[str]
) -> None:
    assert run(tmp_path, "--non-interactive", *args) == 2
    assert not env_file(tmp_path).exists()


@pytest.mark.parametrize("flag", ["--openai-key-env", "--anthropic-key-env"])
def test_a_missing_environment_variable_names_the_option_never_its_value(
    tmp_path: Path,
    no_tty: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    flag: str,
) -> None:
    # Some keys look like variable names (gsk_...): a key pasted as VAR is never echoed.
    looks_like_a_name = f"gsk_{secrets.token_hex(20)}"
    monkeypatch.delenv(looks_like_a_name, raising=False)
    assert run(tmp_path, "--non-interactive", flag, looks_like_a_name) == 2
    text = output(capsys, caplog)
    assert looks_like_a_name not in text
    assert flag in text
    assert not env_file(tmp_path).exists()


def test_stdin_key_from_a_terminal_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", _Terminal("never read" + NL))
    assert run(tmp_path, "--non-interactive", "--openai-key-stdin") == 2
    assert "pipe" in capsys.readouterr().err
    assert not env_file(tmp_path).exists()


def test_a_huge_stdin_is_refused_naming_the_variable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    huge = "sk-" + "a1" * 5000
    monkeypatch.setattr("sys.stdin", io.StringIO(huge))
    assert run(tmp_path, "--non-interactive", "--openai-key-stdin") == 2
    text = output(capsys, caplog)
    assert "ANTIFAZ_OPENAI_API_KEY" in text
    assert "a1a1a1a1" not in text
    assert not env_file(tmp_path).exists()


def test_a_bom_before_the_key_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, openai_key: str, anthropic_key: str
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(BOM + openai_key + NL))
    monkeypatch.setenv("CI_ANTHROPIC", BOM + anthropic_key)
    code = run(
        tmp_path,
        "--non-interactive",
        "--openai-key-stdin",
        "--anthropic-key-env",
        "CI_ANTHROPIC",
    )
    assert code == 0
    values = dotenv_values(env_file(tmp_path))
    assert values["ANTIFAZ_OPENAI_API_KEY"] == openai_key
    assert values["ANTIFAZ_ANTHROPIC_API_KEY"] == anthropic_key


def test_a_getpass_that_would_echo_is_refused(
    tmp_path: Path,
    tty: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def echoing_getpass(prompt: str = "", stream: Any = None) -> str:
        warnings.warn("Can not control echo on the terminal.", getpass.GetPassWarning, stacklevel=2)
        return "sk-would-have-been-echoed"

    monkeypatch.setattr("getpass.getpass", echoing_getpass)
    assert run(tmp_path) == 2
    err = capsys.readouterr().err
    assert "hidden" in err
    assert "would-have-been-echoed" not in err
    assert not env_file(tmp_path).exists()


def test_a_bad_path_is_not_echoed(
    tmp_path: Path, no_tty: None, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_looking = tmp_path / f"sk-{secrets.token_hex(12)}"
    assert main(["init", "--path", str(secret_looking), "--non-interactive"]) == 2
    captured = capsys.readouterr()
    assert secret_looking.name not in captured.out + captured.err


@pytest.mark.parametrize(
    "raw",
    [
        "{key}",
        "  {key}  ",
        '"{key}"',
        "'{key}'",
        "Bearer {key}",
        "bearer {key}",
        ' "Bearer {key}"\n',
    ],
)
def test_pasted_keys_lose_spaces_quotes_and_bearer(raw: str, openai_key: str) -> None:
    assert init_module.clean_provider_key(raw.format(key=openai_key)) == openai_key


@pytest.mark.parametrize(
    "bad",
    [
        "change-me-openai-provider-key",  # the .env.example value
        "sk-has a space-{tail}",
        "sk-ñ-{tail}",
        "sk-dollar$-{tail}",
        "sk-hash#-{tail}",
        'sk-quote"-{tail}',
        "sk-back\\slash-{tail}",
    ],
)
def test_a_bad_provider_key_is_refused_without_showing_it(
    tmp_path: Path,
    no_tty: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    bad: str,
) -> None:
    value = bad.format(tail=secrets.token_hex(12))
    monkeypatch.setenv("CI_OPENAI", value)
    assert run(tmp_path, "--non-interactive", "--openai-key-env", "CI_OPENAI") == 2
    text = output(capsys, caplog)
    assert value not in text
    assert "ANTIFAZ_OPENAI_API_KEY" in text
    assert not env_file(tmp_path).exists()
    assert leftovers(tmp_path) == []


def test_an_unusual_prefix_only_warns(
    tmp_path: Path,
    no_tty: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    key = canary("proxy-")
    monkeypatch.setenv("CI_ANTHROPIC", key)
    assert run(tmp_path, "--non-interactive", "--anthropic-key-env", "CI_ANTHROPIC") == 0
    captured = capsys.readouterr()
    assert "warning" in captured.err.lower()
    assert key not in captured.out + captured.err + caplog.text
    loaded = settings_of(env_file(tmp_path))
    assert loaded.anthropic_api_key is not None


def test_provider_keys_in_the_process_environment_are_not_copied(
    tmp_path: Path, no_tty: None, monkeypatch: pytest.MonkeyPatch, openai_key: str
) -> None:
    monkeypatch.setenv("ANTIFAZ_OPENAI_API_KEY", openai_key)
    assert run(tmp_path, "--non-interactive") == 0
    assert openai_key not in env_file(tmp_path).read_text(encoding="utf-8")


# --- No key on the command line ---------------------------------------------------------------


def _init_parser() -> argparse.ArgumentParser:
    parser = _parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices["init"]  # type: ignore[no-any-return]
    raise AssertionError("no subcommands")


def test_no_option_of_init_takes_a_key_value() -> None:
    takes_value = {
        dest
        for action in _init_parser()._actions
        if action.nargs != 0 and (dest := action.dest) != "help"
    }
    assert takes_value == {"path", "allowed_hosts", "openai_key_env", "anthropic_key_env"}


@pytest.mark.parametrize("flag", ["--openai-key-env", "--anthropic-key-env"])
def test_a_key_passed_as_variable_name_is_refused_and_not_echoed(
    tmp_path: Path,
    no_tty: None,
    capsys: pytest.CaptureFixture[str],
    flag: str,
    openai_key: str,
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run(tmp_path, "--non-interactive", flag, openai_key)
    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    assert openai_key not in captured.out + captured.err
    assert not env_file(tmp_path).exists()


def test_unknown_options_are_not_echoed(
    tmp_path: Path, no_tty: None, capsys: pytest.CaptureFixture[str], openai_key: str
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run(tmp_path, "--non-interactive", "--openai-key", openai_key)
    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    assert openai_key not in captured.out + captured.err


# --- Interactive -----------------------------------------------------------------------------


def test_interactive_asks_the_keys_with_getpass(
    tmp_path: Path,
    tty: None,
    fake_getpass: FakeGetpass,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    openai_key: str,
    anthropic_key: str,
) -> None:
    def no_input(prompt: str = "") -> str:
        raise AssertionError("keys must never be read with input()")

    monkeypatch.setattr("builtins.input", no_input)
    fake_getpass.answers.extend([f'"Bearer {openai_key}"', f"  {anthropic_key} "])
    assert run(tmp_path) == 0
    assert fake_getpass.answers == []
    assert len(fake_getpass.prompts) == 2
    loaded = settings_of(env_file(tmp_path))
    assert loaded.openai_api_key is not None
    assert loaded.openai_api_key.get_secret_value() == openai_key
    assert loaded.anthropic_api_key is not None
    assert loaded.anthropic_api_key.get_secret_value() == anthropic_key
    text = output(capsys, caplog)
    assert openai_key not in text and anthropic_key not in text


def test_interactive_empty_provider_leaves_its_line_out(
    tmp_path: Path, tty: None, fake_getpass: FakeGetpass, anthropic_key: str
) -> None:
    fake_getpass.answers.extend(["", anthropic_key])
    assert run(tmp_path) == 0
    values = dotenv_values(env_file(tmp_path))
    assert "ANTIFAZ_OPENAI_API_KEY" not in values
    assert values["ANTIFAZ_ANTHROPIC_API_KEY"] == anthropic_key


def test_interactive_bad_key_is_refused_without_showing_it(
    tmp_path: Path,
    tty: None,
    fake_getpass: FakeGetpass,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad = f"sk-with space {secrets.token_hex(8)}"
    fake_getpass.answers.extend([bad, ""])
    assert run(tmp_path) == 2
    captured = capsys.readouterr()
    assert bad not in captured.out + captured.err
    assert not env_file(tmp_path).exists()


def test_interactive_without_a_terminal_exits_2(
    tmp_path: Path,
    no_tty: None,
    fake_getpass: FakeGetpass,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run(tmp_path) == 2
    err = capsys.readouterr().err
    assert "-it" in err and "--non-interactive" in err
    assert fake_getpass.prompts == []
    assert not env_file(tmp_path).exists()


def test_stdin_keys_need_non_interactive(tmp_path: Path, tty: None) -> None:
    assert run(tmp_path, "--openai-key-stdin") == 2
    assert not env_file(tmp_path).exists()


# --- Showing the gateway key ------------------------------------------------------------------


def _written_key(tmp_path: Path) -> str:
    value = dotenv_values(env_file(tmp_path))["ANTIFAZ_API_KEY"]
    assert value
    return value


def test_the_key_is_not_shown_when_the_output_is_not_a_terminal(
    tmp_path: Path, no_tty: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path, "--non-interactive") == 0
    captured = capsys.readouterr()
    assert _written_key(tmp_path) not in captured.out + captured.err
    assert "--show-key" in captured.out + captured.err


def test_show_key_prints_it_once(
    tmp_path: Path, no_tty: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path, "--non-interactive", "--show-key") == 0
    captured = capsys.readouterr()
    assert (captured.out + captured.err).count(_written_key(tmp_path)) == 1


def test_on_a_terminal_the_key_is_shown_once_with_a_warning(
    tmp_path: Path, no_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    terminal = _Terminal()
    monkeypatch.setattr("sys.stdout", terminal)
    assert run(tmp_path, "--non-interactive") == 0
    shown = terminal.getvalue()
    assert shown.count(_written_key(tmp_path)) == 1
    assert "only" in shown.lower()


# --- The panel token (issue 53, ADR-0018) ------------------------------------------------------


def _written_token(tmp_path: Path) -> str:
    value = dotenv_values(env_file(tmp_path))["ANTIFAZ_ADMIN_TOKEN"]
    assert value
    return value


def test_init_always_writes_a_panel_token_of_64_hex_different_from_the_key(
    tmp_path: Path, no_tty: None
) -> None:
    assert run(tmp_path, "--non-interactive") == 0
    token = _written_token(tmp_path)
    assert re.fullmatch(r"[0-9a-f]{64}", token)
    assert token != _written_key(tmp_path)
    loaded = settings_of(env_file(tmp_path))
    check_safe_to_start(loaded)
    assert loaded.admin_token is not None
    assert loaded.admin_token.get_secret_value() == token
    assert loaded.trusted_proxies == []


# Random-looking draws made at runtime (no key-like literal in the source).
FIRST = secrets.token_hex(32)
SECOND = secrets.token_hex(32)


def test_a_panel_token_equal_to_the_key_is_drawn_again() -> None:
    draws = iter([FIRST, FIRST, SECOND])
    assert init_module.generate_admin_token(FIRST, lambda _: next(draws)) == SECOND


def test_a_panel_token_that_always_equals_the_key_gives_up() -> None:
    with pytest.raises(init_module.InitError):
        init_module.generate_admin_token(FIRST, lambda _: FIRST)


def test_the_panel_token_is_not_shown_when_the_output_is_not_a_terminal(
    tmp_path: Path, no_tty: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path, "--non-interactive") == 0
    shown = "".join(capsys.readouterr())
    assert _written_token(tmp_path) not in shown
    assert _written_token(tmp_path)[:16] not in shown
    assert "ANTIFAZ_ADMIN_TOKEN" in shown
    assert "http://localhost:8000/panel" in shown
    assert "delete the ANTIFAZ_ADMIN_TOKEN line" in shown


def test_show_key_prints_the_panel_token_once(
    tmp_path: Path, no_tty: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path, "--non-interactive", "--show-key") == 0
    shown = "".join(capsys.readouterr())
    assert shown.count(_written_token(tmp_path)) == 1
    assert shown.count(_written_key(tmp_path)) == 1
    assert "http://localhost:8000/panel" in shown
    assert "delete the ANTIFAZ_ADMIN_TOKEN line" in shown


def test_on_a_terminal_the_panel_token_is_shown_once(
    tmp_path: Path, no_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    terminal = _Terminal()
    monkeypatch.setattr("sys.stdout", terminal)
    assert run(tmp_path, "--non-interactive") == 0
    shown = terminal.getvalue()
    assert shown.count(_written_token(tmp_path)) == 1
    assert "http://localhost:8000/panel" in shown


def test_a_canary_panel_token_never_reaches_output_that_is_not_a_terminal(
    tmp_path: Path,
    no_tty: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    token = "c0ffee" + secrets.token_hex(29)  # a canary, different on every run
    # The key still comes from the real generator; only the panel token is the canary.
    monkeypatch.setattr(init_module, "generate_admin_token", lambda key: token)
    assert run(tmp_path, "--non-interactive") == 0
    assert _written_token(tmp_path) == token
    assert token not in output(capsys, caplog)


def test_deleting_the_panel_token_line_turns_the_panel_off(tmp_path: Path, no_tty: None) -> None:
    assert run(tmp_path, "--non-interactive") == 0
    path = env_file(tmp_path)
    kept = [x for x in path.read_text(encoding="utf-8").splitlines() if "ADMIN_TOKEN=" not in x]
    path.write_text(NL.join(kept) + NL, encoding="utf-8", newline=NL)
    loaded = settings_of(path)
    check_safe_to_start(loaded)
    assert loaded.admin_token is None


# --- An existing .env -------------------------------------------------------------------------


OLD = b"ANTIFAZ_LOG_LEVEL=DEBUG\n# the old file\n"


def backups(tmp_path: Path) -> list[Path]:
    return sorted(tmp_path.glob(".env.bak-*"))


def test_existing_env_without_force_is_left_untouched(
    tmp_path: Path, no_tty: None, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file(tmp_path).write_bytes(OLD)
    assert run(tmp_path, "--non-interactive") == 2
    assert env_file(tmp_path).read_bytes() == OLD
    assert backups(tmp_path) == []
    assert "--force" in capsys.readouterr().err


@pytest.mark.parametrize("answer", ["", "no", "y", "YES please"])
def test_interactive_needs_the_word_yes(
    tmp_path: Path,
    tty: None,
    fake_getpass: FakeGetpass,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
) -> None:
    env_file(tmp_path).write_bytes(OLD)
    monkeypatch.setattr("builtins.input", lambda prompt="": answer)
    assert run(tmp_path) == 2
    assert env_file(tmp_path).read_bytes() == OLD
    assert backups(tmp_path) == []
    assert fake_getpass.prompts == []


def test_interactive_yes_replaces_and_keeps_a_backup(
    tmp_path: Path, tty: None, fake_getpass: FakeGetpass, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file(tmp_path).write_bytes(OLD)
    monkeypatch.setattr("builtins.input", lambda prompt="": " yes ")
    fake_getpass.answers.extend(["", ""])
    assert run(tmp_path) == 0
    [backup] = backups(tmp_path)
    assert backup.read_bytes() == OLD
    assert re.fullmatch(r"\.env\.bak-\d{8}-\d{6}", backup.name)
    assert env_file(tmp_path).read_bytes() != OLD


def test_backups_in_the_same_second_never_overwrite_each_other(
    tmp_path: Path, no_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(init_module, "_now", lambda: datetime(2026, 10, 6, 12, 0, 0))
    env_file(tmp_path).write_bytes(OLD)
    contents = [OLD]
    for _ in range(3):
        assert run(tmp_path, "--non-interactive", "--force") == 0
        contents.append(env_file(tmp_path).read_bytes())
    names = [b.name for b in backups(tmp_path)]
    assert names == [
        ".env.bak-20261006-120000",
        ".env.bak-20261006-120000-1",
        ".env.bak-20261006-120000-2",
    ]
    assert [b.read_bytes() for b in backups(tmp_path)] == contents[:3]


def test_a_directory_named_env_is_refused(tmp_path: Path, no_tty: None) -> None:
    env_file(tmp_path).mkdir()
    assert run(tmp_path, "--non-interactive", "--force") == 2
    assert env_file(tmp_path).is_dir()


def test_a_missing_folder_is_refused(tmp_path: Path, no_tty: None) -> None:
    assert main(["init", "--path", str(tmp_path / "nope"), "--non-interactive"]) == 2
    assert not (tmp_path / "nope").exists()


# --- Atomic write -----------------------------------------------------------------------------


def test_a_failed_replace_keeps_the_original_and_removes_the_temp_file(
    tmp_path: Path,
    no_tty: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    openai_key: str,
) -> None:
    env_file(tmp_path).write_bytes(OLD)
    monkeypatch.setenv("CI_OPENAI", openai_key)

    def broken_replace(src: Any, dst: Any) -> None:
        raise OSError(f"disk full while writing {openai_key}")

    monkeypatch.setattr(init_module, "_sleep", lambda seconds: None)
    monkeypatch.setattr(os, "replace", broken_replace)
    code = run(tmp_path, "--non-interactive", "--force", "--openai-key-env", "CI_OPENAI")
    assert code == 1
    assert env_file(tmp_path).read_bytes() == OLD
    assert leftovers(tmp_path) == []
    text = output(capsys, caplog)
    assert openai_key not in text
    assert "disk full" not in text


def test_a_env_that_appears_meanwhile_is_not_replaced(tmp_path: Path) -> None:
    env_file(tmp_path).write_bytes(OLD)
    content = init_module.render({"ANTIFAZ_API_KEY": init_module.generate_gateway_key()})
    with pytest.raises(init_module.InitError):
        init_module.write_env(tmp_path, content, replace_existing=False)
    assert env_file(tmp_path).read_bytes() == OLD
    assert backups(tmp_path) == []
    assert leftovers(tmp_path) == []


def test_a_locked_file_on_windows_is_retried(
    tmp_path: Path, no_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_replace = os.replace
    attempts: list[int] = []

    def flaky_replace(src: Any, dst: Any) -> None:
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError("locked")
        real_replace(src, dst)

    monkeypatch.setattr(init_module, "_sleep", lambda seconds: None)
    monkeypatch.setattr(os, "replace", flaky_replace)
    assert run(tmp_path, "--non-interactive") == 0
    assert len(attempts) == 3
    assert leftovers(tmp_path) == []


@pytest.mark.parametrize("error", [KeyboardInterrupt, ValueError])
def test_the_temp_file_is_always_removed(
    tmp_path: Path,
    no_tty: None,
    monkeypatch: pytest.MonkeyPatch,
    error: type[BaseException],
) -> None:
    env_file(tmp_path).write_bytes(OLD)

    def explode(settings: Settings) -> None:
        raise error()

    monkeypatch.setattr(init_module, "check_safe_to_start", explode)
    with contextlib.suppress(KeyboardInterrupt, ValueError):
        run(tmp_path, "--non-interactive", "--force")
    assert leftovers(tmp_path) == []
    assert env_file(tmp_path).read_bytes() == OLD


def test_a_failure_after_the_backup_says_the_backup_exists(
    tmp_path: Path,
    no_tty: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file(tmp_path).write_bytes(OLD)

    def broken_replace(src: Any, dst: Any) -> None:
        raise OSError("broken")

    monkeypatch.setattr(os, "replace", broken_replace)
    assert run(tmp_path, "--non-interactive", "--force") == 1
    err = capsys.readouterr().err
    [backup] = backups(tmp_path)
    assert backup.name in err
    assert "nothing was changed" not in err
    assert env_file(tmp_path).read_bytes() == OLD
    assert leftovers(tmp_path) == []


def test_a_validation_failure_keeps_the_original(
    tmp_path: Path,
    no_tty: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    openai_key: str,
) -> None:
    env_file(tmp_path).write_bytes(OLD)
    monkeypatch.setenv("CI_OPENAI", openai_key)

    def refuse(settings: Settings) -> None:
        raise UnsafeConfigError("ANTIFAZ_API_KEY is too short")

    monkeypatch.setattr(init_module, "check_safe_to_start", refuse)
    code = run(tmp_path, "--non-interactive", "--force", "--openai-key-env", "CI_OPENAI")
    assert code == 1
    assert env_file(tmp_path).read_bytes() == OLD
    assert backups(tmp_path) == []
    assert leftovers(tmp_path) == []
    assert openai_key not in output(capsys, caplog)


def test_validation_reads_the_file_alone_not_the_process_environment(
    tmp_path: Path, no_tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A bad environment cannot fail a good file...
    monkeypatch.setenv("ANTIFAZ_API_KEY", "short")
    monkeypatch.setenv("ANTIFAZ_ALLOWED_HOSTS", "*")
    assert run(tmp_path, "--non-interactive") == 0
    # ...and a good environment cannot rescue a bad file.
    monkeypatch.setenv("ANTIFAZ_ALLOWED_HOSTS", "localhost")
    other = tmp_path / "other"
    other.mkdir()
    assert main(["init", "--path", str(other), "--non-interactive", "--allowed-hosts", "*"]) == 2
    assert not env_file(other).exists()


def test_allowed_hosts_are_written(tmp_path: Path, no_tty: None) -> None:
    assert run(tmp_path, "--non-interactive", "--allowed-hosts", "antifaz.internal,localhost") == 0
    assert settings_of(env_file(tmp_path)).allowed_hosts == ["antifaz.internal", "localhost"]


@pytest.mark.parametrize("hosts", ["", "a b", "x$y", "host#1", "*.com"])
def test_bad_allowed_hosts_are_refused(tmp_path: Path, no_tty: None, hosts: str) -> None:
    assert run(tmp_path, "--non-interactive", "--allowed-hosts", hosts) == 2
    assert not env_file(tmp_path).exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_env_and_backup_are_only_readable_by_the_owner(tmp_path: Path, no_tty: None) -> None:
    env_file(tmp_path).write_bytes(OLD)
    env_file(tmp_path).chmod(0o644)
    assert run(tmp_path, "--non-interactive", "--force") == 0
    assert stat.S_IMODE(env_file(tmp_path).stat().st_mode) == 0o600
    [backup] = backups(tmp_path)
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


# --- No network -------------------------------------------------------------------------------


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("antifaz init must not use the network")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    yield


def test_init_never_uses_the_network(
    tmp_path: Path, no_tty: None, no_network: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI_OPENAI", canary("sk-"))
    assert run(tmp_path, "--non-interactive", "--openai-key-env", "CI_OPENAI") == 0


def test_a_backup_comes_with_a_reminder_that_it_holds_the_old_secrets(
    tmp_path: Path, no_tty: None, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file(tmp_path).write_bytes(OLD)
    assert run(tmp_path, "--non-interactive", "--force") == 0
    shown = "".join(capsys.readouterr())
    assert backups(tmp_path)[0].name in shown
    assert "holds the old keys and panel token" in shown
    assert "delete it once you have rotated them" in shown


def test_without_a_backup_there_is_no_reminder(
    tmp_path: Path, no_tty: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path, "--non-interactive") == 0
    assert "holds the old keys" not in "".join(capsys.readouterr())
