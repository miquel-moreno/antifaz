"""`antifaz setup claude-code` points Claude Code at Antifaz, never writing a key (issue 31).

Every test works in pytest's tmp_path: HOME, USERPROFILE and CLAUDE_CONFIG_DIR point there and
the current folder is a temporary one, so the real Claude Code settings of this machine are
never read or written. Keys are canaries made at runtime (no key-like literal in the source).
"""

import io
import json
import os
import secrets
import stat
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from antifaz.cli import init as init_module
from antifaz.cli import main
from antifaz.cli import setup_claude_code as setup

URL = "http://127.0.0.1:8000"
BOM = b"\xef\xbb\xbf"


def canary(prefix: str = "") -> str:
    return f"{prefix}canary{secrets.token_hex(24)}"


@pytest.fixture(autouse=True)
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fake home and config folder; the real ones are never reachable."""
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    for name in ("ANTIFAZ_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(work)
    monkeypatch.setattr(setup, "_managed_folder", lambda: None)
    monkeypatch.setattr(setup, "_stdin_is_terminal", lambda: False)
    yield home


@pytest.fixture
def settings_file(sandbox: Path) -> Path:
    return sandbox / ".claude" / "settings.json"


def write(path: Path, data: Any) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, indent=2).encode() + b"\n"
    path.write_bytes(raw)
    return raw


def run(*args: str) -> int:
    return main(["setup", "claude-code", *args])


def files_in(folder: Path) -> set[str]:
    return {p.name for p in folder.iterdir()} if folder.exists() else set()


# --- Target ----------------------------------------------------------------------------------


def test_user_settings_honour_claude_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
    assert setup.user_settings_path() == tmp_path / "elsewhere" / "settings.json"


def test_without_claude_config_dir_the_home_folder_is_used(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Path.home() reads USERPROFILE on Windows and HOME elsewhere: both point at the sandbox."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert setup.user_settings_path() == sandbox / ".claude" / "settings.json"


def test_an_empty_claude_config_dir_falls_back_to_home(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "  ")
    assert setup.user_settings_path() == sandbox / ".claude" / "settings.json"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths")
def test_windows_backslash_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = str(tmp_path / "Claude Config").replace("/", "\\")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", folder)
    assert run("--apply", "--yes") == 0
    assert (tmp_path / "Claude Config" / "settings.json").is_file()


def test_project_writes_settings_local_json_in_the_current_folder(
    tmp_path: Path, settings_file: Path
) -> None:
    assert run("--project", "--apply", "--yes") == 0
    local = tmp_path / "work" / ".claude" / "settings.local.json"
    assert json.loads(local.read_bytes()) == {"env": {"ANTHROPIC_BASE_URL": URL}}
    assert not settings_file.exists()
    assert not (tmp_path / "work" / ".claude" / "settings.json").exists()


def test_the_shared_settings_json_of_a_repository_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(repo / ".claude"))
    assert run("--apply", "--yes") == 2
    assert "shared" in capsys.readouterr().err
    assert not (repo / ".claude").exists()


def test_a_config_dir_that_is_the_projects_claude_folder_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "work" / ".claude"))
    assert run("--apply", "--yes") == 2
    assert not (tmp_path / "work" / ".claude").exists()


# --- Dry run and apply -----------------------------------------------------------------------


def test_dry_run_writes_nothing(
    sandbox: Path, settings_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = write(settings_file, {"model": "opus"})
    before = files_in(settings_file.parent)
    assert run() == 0
    out = capsys.readouterr().out
    assert settings_file.read_bytes() == raw
    assert files_in(settings_file.parent) == before
    assert '+    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8000"' in out
    assert "Dry run" in out
    assert "SHA-256" in out


def test_dry_run_on_a_missing_file_creates_nothing(sandbox: Path) -> None:
    assert run() == 0
    assert not (sandbox / ".claude").exists()


def test_missing_file_is_created_with_only_our_entry(settings_file: Path) -> None:
    assert run("--apply", "--yes") == 0
    assert json.loads(settings_file.read_bytes()) == {"env": {"ANTHROPIC_BASE_URL": URL}}
    assert files_in(settings_file.parent) == {"settings.json"}  # no backup, no temp file


def test_other_tools_entries_are_kept(settings_file: Path) -> None:
    original = {
        "permissions": {"allow": ["Bash(make test)"], "deny": ["Read(./.env)"]},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command"}]}]},
        "env": {"OTHER": "1"},
        "model": "opus",
        "nested": {"list": [1, 2.5, None, True, "x"]},
    }
    write(settings_file, original)
    assert run("--apply", "--yes", "--key-helper", "get-antifaz-key") == 0
    new = json.loads(settings_file.read_bytes())
    assert new == {
        **original,
        "env": {"OTHER": "1", "ANTHROPIC_BASE_URL": URL},
        "apiKeyHelper": "get-antifaz-key",
    }
    assert list(new) == [*original, "apiKeyHelper"]  # order kept, new keys last


def test_a_backup_is_kept_and_never_overwritten(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(init_module, "_now", lambda: datetime(2026, 10, 6, 12, 0, 0))
    first = write(settings_file, {"model": "opus"})
    assert run("--apply", "--yes") == 0
    second = settings_file.read_bytes()
    assert run("--uninstall", "--apply", "--yes") == 0
    folder = settings_file.parent
    assert (folder / "settings.json.bak-20261006-120000").read_bytes() == first
    assert (folder / "settings.json.bak-20261006-120000-1").read_bytes() == second


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_new_file_and_backup_are_owner_only(settings_file: Path) -> None:
    write(settings_file, {})
    assert run("--apply", "--yes") == 0
    for path in settings_file.parent.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name


def test_a_bom_and_crlf_are_kept(settings_file: Path) -> None:
    settings_file.parent.mkdir(parents=True)
    settings_file.write_bytes(BOM + b'{\r\n  "model": "opus"\r\n}\r\n')
    assert run("--apply", "--yes") == 0
    raw = settings_file.read_bytes()
    assert raw.startswith(BOM)
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert json.loads(raw[len(BOM) :]) == {"model": "opus", "env": {"ANTHROPIC_BASE_URL": URL}}


def test_written_json_uses_two_space_indent(settings_file: Path) -> None:
    settings_file.parent.mkdir(parents=True)
    settings_file.write_bytes(b'{"model":"opus"}')
    assert run("--apply", "--yes") == 0
    assert settings_file.read_text(encoding="utf-8").splitlines()[1] == '  "model": "opus",'


def test_running_twice_changes_nothing(
    settings_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("--apply", "--yes") == 0
    raw = settings_file.read_bytes()
    capsys.readouterr()
    assert run("--apply", "--yes") == 0
    assert "nothing to do" in capsys.readouterr().out
    assert settings_file.read_bytes() == raw
    assert files_in(settings_file.parent) == {"settings.json"}


def test_a_trailing_slash_in_the_url_is_dropped(settings_file: Path) -> None:
    assert run("--url", "https://antifaz.example.com/", "--apply", "--yes") == 0
    env = json.loads(settings_file.read_bytes())["env"]
    assert env == {"ANTHROPIC_BASE_URL": "https://antifaz.example.com"}


# --- Refusals --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        b'{"model": "opus",}',
        b"{not json",
        b'{"a": 1, "a": 2}',
        b'{"env": {"X": "1", "X": "2"}}',
        b"[1, 2]",
        b'"text"',
        b'{"env": "nope"}',
        b'{"env": null}',
        b'{"a": NaN}',
        b'{"a": "\xff"}',
        b"[" * 100_000,
    ],
    ids=[
        "trailing-comma",
        "broken",
        "duplicate-top",
        "duplicate-nested",
        "array",
        "string",
        "env-string",
        "env-null",
        "nan",
        "not-utf8",
        "too-deep",
    ],
)
def test_unreadable_settings_abort_without_touching_anything(
    settings_file: Path, raw: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    settings_file.parent.mkdir(parents=True)
    settings_file.write_bytes(raw)
    assert run("--apply", "--yes") == 2
    assert settings_file.read_bytes() == raw
    assert files_in(settings_file.parent) == {"settings.json"}
    assert "fix it first" in capsys.readouterr().err


def test_a_huge_file_is_refused(settings_file: Path) -> None:
    settings_file.parent.mkdir(parents=True)
    raw = b'{"a": "' + b"x" * setup.MAX_SETTINGS_BYTES + b'"}'
    settings_file.write_bytes(raw)
    assert run("--apply", "--yes") == 2
    assert settings_file.read_bytes() == raw


def test_a_folder_in_place_of_the_file_is_refused(settings_file: Path) -> None:
    settings_file.mkdir(parents=True)
    assert run("--apply", "--yes") == 2
    assert settings_file.is_dir()


def test_a_link_is_refused(tmp_path: Path, settings_file: Path) -> None:
    real = tmp_path / "dotfiles.json"
    real.write_text("{}", encoding="utf-8")
    settings_file.parent.mkdir(parents=True)
    try:
        settings_file.symlink_to(real)
    except OSError:
        pytest.skip("this system cannot make symlinks")
    assert run("--apply", "--yes") == 2
    assert settings_file.is_symlink()
    assert real.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://127.0.0.1:8000",
        "http://user:pw@127.0.0.1:8000",
        "http://127.0.0.1:8000/?a=1",
        "http://127.0.0.1:8000/#x",
        "http://127.0.0.1:8000?",
        "http://127.0.0.1:99999",
        "http://",
        "http://127.0.0.1:8000/ x",
        "http://127.0.0.1:8000/\n",
    ],
)
def test_a_bad_url_is_refused_without_repeating_it(
    settings_file: Path, url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("--url", url, "--apply", "--yes") == 2
    captured = capsys.readouterr()
    assert url.strip() not in captured.out + captured.err or url.strip() == "http://"
    assert not settings_file.exists()


def test_a_url_holding_a_key_is_refused(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    key = canary()
    monkeypatch.setenv("ANTIFAZ_API_KEY", key)
    assert run("--url", f"http://127.0.0.1:8000/{key}", "--apply", "--yes") == 2
    captured = capsys.readouterr()
    assert key not in captured.out + captured.err
    assert not settings_file.exists()


@pytest.mark.parametrize(
    "helper",
    [
        f"echo {secrets.token_hex(32)}",
        "echo sk-" + "ant-" + "abcdefghijklmnop",
        "",
        "get-key\nrm -rf /",
        "x" * (setup.MAX_HELPER + 1),
    ],
)
def test_a_helper_that_holds_a_key_or_is_odd_is_refused(
    settings_file: Path, helper: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("--key-helper", helper, "--apply", "--yes") == 2
    captured = capsys.readouterr()
    if helper.strip():
        assert helper not in captured.out + captured.err
    assert not settings_file.exists()


def test_a_helper_holding_the_antifaz_key_from_the_environment_is_refused(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    key = "Short-Key_" + secrets.token_hex(4)  # not key-like by shape, but it IS the key
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", key)
    assert run("--key-helper", f"printf {key}", "--apply", "--yes") == 2
    captured = capsys.readouterr()
    assert key not in captured.out + captured.err
    assert not settings_file.exists()


def test_a_normal_helper_is_written_with_a_warning(
    settings_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    helper = r"powershell -NoProfile -File C:\scripts\get-antifaz-key.ps1"
    assert run("--key-helper", helper, "--apply", "--yes") == 0
    assert json.loads(settings_file.read_bytes())["apiKeyHelper"] == helper
    captured = capsys.readouterr()
    assert "through your shell" in captured.err
    assert "nothing else to set" in captured.out


def test_apply_without_a_terminal_needs_yes(settings_file: Path) -> None:
    assert run("--apply") == 2
    assert not settings_file.exists()


def test_yes_without_apply_is_refused(settings_file: Path) -> None:
    assert run("--yes") == 2
    assert not settings_file.exists()


def test_apply_asks_and_anything_but_yes_changes_nothing(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = write(settings_file, {"model": "opus"})
    monkeypatch.setattr(setup, "_stdin_is_terminal", lambda: True)
    monkeypatch.setattr(setup, "_ask", lambda prompt: "y")
    assert run("--apply") == 2
    assert settings_file.read_bytes() == raw
    monkeypatch.setattr(setup, "_ask", lambda prompt: "yes")
    assert run("--apply") == 0
    assert json.loads(settings_file.read_bytes())["env"] == {"ANTHROPIC_BASE_URL": URL}


def test_ctrl_c_at_the_question_changes_nothing(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = write(settings_file, {})

    def interrupt(prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(setup, "_stdin_is_terminal", lambda: True)
    monkeypatch.setattr(setup, "_ask", interrupt)
    assert run("--apply") == 2
    assert settings_file.read_bytes() == raw


def test_a_change_between_the_diff_and_the_write_aborts(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write(settings_file, {"model": "opus"})
    edited = json.dumps({"model": "sonnet"}).encode()

    def someone_edits(prompt: str) -> str:
        settings_file.write_bytes(edited)  # Claude Code or an editor saves meanwhile
        return "yes"

    monkeypatch.setattr(setup, "_stdin_is_terminal", lambda: True)
    monkeypatch.setattr(setup, "_ask", someone_edits)
    assert run("--apply") == 1
    assert "changed meanwhile; run again" in capsys.readouterr().err
    assert settings_file.read_bytes() == edited
    assert files_in(settings_file.parent) == {"settings.json"}


def test_a_file_that_appears_meanwhile_is_not_replaced(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def someone_creates(prompt: str) -> str:
        write(settings_file, {"model": "opus"})
        return "yes"

    monkeypatch.setattr(setup, "_stdin_is_terminal", lambda: True)
    monkeypatch.setattr(setup, "_ask", someone_creates)
    assert run("--apply") == 1
    assert json.loads(settings_file.read_bytes()) == {"model": "opus"}


def test_a_failed_replace_leaves_the_old_file_and_names_the_backup(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = write(settings_file, {"model": "opus"})

    def locked(source: Path, target: Path) -> None:
        raise PermissionError

    monkeypatch.setattr(init_module, "_replace", locked)
    assert run("--apply", "--yes") == 1
    assert settings_file.read_bytes() == raw
    assert "a copy is in settings.json.bak-" in capsys.readouterr().err
    assert not [p for p in settings_file.parent.iterdir() if ".tmp-" in p.name]


def test_replace_is_retried_when_windows_holds_the_file(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(settings_file, {})
    calls = []
    real_replace = os.replace

    def flaky(source: Any, target: Any) -> None:
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError
        real_replace(source, target)

    monkeypatch.setattr(init_module.os, "replace", flaky)
    monkeypatch.setattr(init_module, "_sleep", lambda seconds: None)
    assert run("--apply", "--yes") == 0
    assert len(calls) == 3


def test_a_folder_that_cannot_be_created_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(blocker / "sub"))
    assert run("--apply", "--yes") == 1


# --- Warnings --------------------------------------------------------------------------------


def test_a_key_already_in_env_gives_a_warning_without_its_value(
    settings_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api_key, token = canary("sk-" + "ant-"), canary()
    write(settings_file, {"env": {"ANTHROPIC_API_KEY": api_key, "ANTHROPIC_AUTH_TOKEN": token}})
    assert run() == 0
    captured = capsys.readouterr()
    assert "env.ANTHROPIC_API_KEY is set in this file" in captured.err
    assert "env.ANTHROPIC_AUTH_TOKEN is set in this file" in captured.err
    assert "401" in captured.err
    for value in (api_key, token):
        assert value not in captured.out + captured.err


def test_a_different_base_url_shows_old_and_new(
    settings_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(settings_file, {"env": {"ANTHROPIC_BASE_URL": "https://gateway.example.com"}})
    assert run() == 0
    captured = capsys.readouterr()
    assert '-    "ANTHROPIC_BASE_URL": "https://gateway.example.com"' in captured.out
    assert '+    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8000"' in captured.out
    assert "points somewhere else" in captured.err


def test_an_old_base_url_with_a_password_is_hidden_in_the_diff(
    settings_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    password = canary()
    write(settings_file, {"env": {"ANTHROPIC_BASE_URL": f"https://u:{password}@gw.example"}})
    assert run() == 0
    captured = capsys.readouterr()
    assert password not in captured.out + captured.err
    assert '-    "ANTHROPIC_BASE_URL": "<hidden>"' in captured.out


def test_managed_settings_are_mentioned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / "managed-settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(setup, "_managed_folder", lambda: managed)
    assert run() == 0
    assert "managed settings file exists" in capsys.readouterr().err


def test_managed_folders_are_the_documented_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    assert setup.MANAGED_FOLDERS["win32"] == r"C:\Program Files\ClaudeCode"
    assert setup.MANAGED_FOLDERS["linux"] == "/etc/claude-code"
    assert setup.MANAGED_FOLDERS["darwin"] == "/Library/Application Support/ClaudeCode"


def test_plain_http_to_another_machine_gives_a_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run("--url", "http://antifaz.lan:8000") == 0
    assert "unencrypted" in capsys.readouterr().err


def test_an_anthropic_key_in_the_shell_gives_a_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    key = canary("sk-" + "ant-")
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    assert run() == 0
    captured = capsys.readouterr()
    assert "ANTHROPIC_API_KEY is set in this shell" in captured.err
    assert key not in captured.out + captured.err


def test_next_steps_name_the_token_variable_and_doctor(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run("--apply", "--yes") == 0
    out = capsys.readouterr().out
    assert "export ANTHROPIC_AUTH_TOKEN=" in out
    assert '[Environment]::SetEnvironmentVariable("ANTHROPIC_AUTH_TOKEN"' in out
    assert "restart Claude Code" in out
    assert "antifaz doctor" in out
    assert "--uninstall --apply" in out


# --- The canary: no key is ever written or shown ----------------------------------------------


def test_keys_in_the_environment_never_reach_the_file_backups_or_output(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    keys = {
        "ANTIFAZ_API_KEY": canary(),
        "ANTHROPIC_AUTH_TOKEN": canary(),
        "ANTHROPIC_API_KEY": canary("sk-" + "ant-"),
    }
    for name, value in keys.items():
        monkeypatch.setenv(name, value)
    user_secret = canary()  # something of the user's own in the file: kept, never shown
    write(settings_file, {"env": {"MY_TOKEN": user_secret}, "permissions": {"allow": ["x"]}})
    assert run() == 0
    assert run("--apply", "--yes", "--key-helper", "get-antifaz-key") == 0
    assert run("--uninstall", "--apply", "--yes", "--key-helper", "get-antifaz-key") == 0
    captured = capsys.readouterr()
    for path in settings_file.parent.iterdir():
        content = path.read_text(encoding="utf-8")
        for value in keys.values():
            assert value not in content, path.name
    for value in (*keys.values(), user_secret):
        assert value not in captured.out + captured.err
    assert json.loads(settings_file.read_bytes())["env"] == {"MY_TOKEN": user_secret}


# --- Uninstall -------------------------------------------------------------------------------


def test_uninstall_removes_exactly_what_was_set(settings_file: Path) -> None:
    original = {"env": {"OTHER": "1"}, "model": "opus"}
    write(settings_file, original)
    assert run("--apply", "--yes", "--key-helper", "get-antifaz-key") == 0
    assert run("--uninstall", "--apply", "--yes", "--key-helper", "get-antifaz-key") == 0
    assert json.loads(settings_file.read_bytes()) == original


def test_uninstall_drops_an_env_left_empty(settings_file: Path) -> None:
    write(settings_file, {"model": "opus"})
    assert run("--apply", "--yes") == 0
    assert run("--uninstall", "--apply", "--yes") == 0
    assert json.loads(settings_file.read_bytes()) == {"model": "opus"}


def test_uninstall_dry_run_shows_the_removal_and_writes_nothing(
    settings_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = write(settings_file, {"env": {"ANTHROPIC_BASE_URL": URL}})
    assert run("--uninstall") == 0
    assert '-    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8000"' in capsys.readouterr().out
    assert settings_file.read_bytes() == raw


def test_uninstall_refuses_a_base_url_someone_changed(
    settings_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = write(settings_file, {"env": {"ANTHROPIC_BASE_URL": "https://other.example"}})
    assert run("--uninstall", "--apply", "--yes") == 2
    assert "not the value this command would set" in capsys.readouterr().err
    assert settings_file.read_bytes() == raw
    assert files_in(settings_file.parent) == {"settings.json"}


def test_uninstall_refuses_a_helper_someone_changed(settings_file: Path) -> None:
    raw = write(settings_file, {"env": {"ANTHROPIC_BASE_URL": URL}, "apiKeyHelper": "other"})
    assert run("--uninstall", "--apply", "--yes", "--key-helper", "mine") == 2
    assert settings_file.read_bytes() == raw


def test_uninstall_without_key_helper_leaves_the_helper_and_says_so(
    settings_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(settings_file, {"env": {"ANTHROPIC_BASE_URL": URL}, "apiKeyHelper": "mine"})
    assert run("--uninstall", "--apply", "--yes") == 0
    assert json.loads(settings_file.read_bytes()) == {"apiKeyHelper": "mine"}
    assert "apiKeyHelper is set and stays" in capsys.readouterr().err


def test_uninstall_with_nothing_to_remove_does_nothing(
    settings_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = write(settings_file, {"model": "opus"})
    assert run("--uninstall", "--apply", "--yes") == 0
    assert "nothing to do" in capsys.readouterr().out
    assert settings_file.read_bytes() == raw


def test_uninstall_on_a_missing_file_does_nothing(sandbox: Path) -> None:
    assert run("--uninstall", "--apply", "--yes") == 0
    assert not (sandbox / ".claude").exists()


# --- Command line ----------------------------------------------------------------------------


def test_setup_needs_a_client_and_never_echoes_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = canary()
    with pytest.raises(SystemExit) as raised:
        main(["setup", secret])
    assert raised.value.code == 2
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err


def test_doctor_no_longer_says_coming_soon() -> None:
    source = Path(init_module.__file__).with_name("doctor.py").read_text(encoding="utf-8")
    assert "coming soon" not in source


# --- Property: apply then uninstall gives back the same object --------------------------------

_KEYS = st.text(min_size=1, max_size=8).filter(lambda k: k not in ("env", "apiKeyHelper"))
_SCALARS = st.none() | st.booleans() | st.integers() | st.text(max_size=12)
_JSON = st.recursive(
    _SCALARS,
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=6), children, max_size=4)
    ),
    max_leaves=12,
)
_ENV = st.dictionaries(
    st.text(min_size=1, max_size=10).filter(lambda k: k != "ANTHROPIC_BASE_URL"),
    st.text(max_size=12),
    min_size=1,
    max_size=4,
)


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    others=st.dictionaries(_KEYS, _JSON, max_size=5),
    env=st.none() | _ENV,
    bom=st.booleans(),
    crlf=st.booleans(),
)
def test_apply_then_uninstall_round_trip(
    settings_file: Path, others: dict[str, Any], env: dict[str, str] | None, bom: bool, crlf: bool
) -> None:
    """Apply then uninstall gives back the same JSON object (formatting aside), BOM and line
    ends included. Not covered by design: an empty `env` (dropped) and our own entries already
    present before (removed)."""
    original: dict[str, Any] = dict(others)
    if env is not None:
        original["env"] = env
    text = json.dumps(original, indent=4, ensure_ascii=False) + "\n"
    if crlf:
        text = text.replace("\n", "\r\n")
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    for old in settings_file.parent.iterdir():
        old.unlink()
    settings_file.write_bytes((BOM if bom else b"") + text.encode("utf-8"))
    out, err = io.StringIO(), io.StringIO()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "stdout", out)
        patch.setattr(sys, "stderr", err)
        assert run("--apply", "--yes", "--key-helper", "get-antifaz-key") == 0
        assert run("--uninstall", "--apply", "--yes", "--key-helper", "get-antifaz-key") == 0
    raw = settings_file.read_bytes()
    assert raw.startswith(BOM) == bom
    assert (b"\r\n" in raw) == crlf
    assert json.loads(raw[len(BOM) :] if bom else raw) == original
