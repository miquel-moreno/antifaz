"""End-to-end of `antifaz doctor` (issue 43) against the real Compose stack and fake provider.

Run from this process against the published port (configuration from a copy of the throwaway
env file, no provider call), and inside the running container with --providers (as the README
says), where the provider base URLs point at the fake provider, which records the model-list
requests. Keys are compared as booleans with fixed messages, never printed.
"""

import os
import shutil
from pathlib import Path

import pytest

from antifaz.cli import main
from tests.e2e.conftest import Stack, _wait_healthy

pytestmark = pytest.mark.e2e


def _keys(stack: Stack) -> tuple[str, str, str]:
    return (stack.gateway_key, stack.openai_key, stack.anthropic_key)


def test_doctor_from_the_host_passes_against_the_running_stack(
    stack: Stack,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _wait_healthy(stack) == "healthy"
    for name in list(os.environ):
        if name.upper().startswith("ANTIFAZ_"):
            monkeypatch.delenv(name, raising=False)
    shutil.copyfile(stack.env_file, tmp_path / ".env")
    try:
        code = main(["doctor", "--path", str(tmp_path), "--url", stack.gateway])
    finally:
        (tmp_path / ".env").unlink()
    out, err = capsys.readouterr()
    assert code == 0, out + err
    assert "gateway: up" in out
    leaked = any(key in out + err for key in _keys(stack))
    assert not leaked, "a key appeared in the doctor output"


def test_doctor_in_the_running_container_checks_the_fake_providers(stack: Stack) -> None:
    """The README's `docker compose exec antifaz antifaz doctor --providers`."""
    assert _wait_healthy(stack) == "healthy"
    before = len(stack.received())
    result = stack.compose("exec", "-T", "antifaz", "antifaz", "doctor", "--providers")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "gateway: up" in result.stdout
    assert "OpenAI: the key works" in result.stdout
    assert "Anthropic: the key works" in result.stdout
    leaked = any(key in result.stdout + result.stderr for key in _keys(stack))
    assert not leaked, "a key appeared in the doctor output"
    lists = [r for r in stack.received()[before:] if r.get("method") == "GET"]
    assert [r["path"] for r in lists] == ["/v1/models", "/v1/models"]
    openai_ok = lists[0]["authorization"] == f"Bearer {stack.openai_key}"
    anthropic_ok = lists[1]["x-api-key"] == stack.anthropic_key
    assert openai_ok and anthropic_ok, "the providers did not get their own keys"
    assert lists[1]["anthropic-version"] == "2023-06-01"
    gateway_key_sent = any(stack.gateway_key in str(r) for r in lists)
    assert not gateway_key_sent, "the Antifaz key reached a provider"
