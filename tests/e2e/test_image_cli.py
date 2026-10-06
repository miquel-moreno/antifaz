"""End-to-end of the one-minute install (issue 42, ADR-0017) with the real image.

`antifaz` is the image's entrypoint and `serve` its default command. `init` runs inside the
image exactly as the README says (`docker run --rm -v "$PWD:/work" -w /work ... init`), with
throwaway fake keys passed by variable NAME (`docker run -e NAME`, so the value is never in an
argument), and the .env it writes starts the gateway with Compose and with the hardened
one-line `docker run`. Everything lives in a temp folder that is deleted at the end; your .env
is never read (Compose gets the generated file as --env-file too).
"""

import os
import secrets
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.e2e.conftest import BASE_FILES, IMAGE, REPO, Stack, _free_port, docker

pytestmark = pytest.mark.e2e

PROJECT = "antifaz-e2e-init"
OPENAI_VAR = "ANTIFAZ_E2E_INIT_OPENAI"
ANTHROPIC_VAR = "ANTIFAZ_E2E_INIT_ANTHROPIC"


def _user() -> list[str]:
    """--user UID:GID on Linux and macOS, as the README says (Docker Desktop on Windows maps it)."""
    if sys.platform == "win32":
        return []
    return ["--user", f"{os.getuid()}:{os.getgid()}"]


def _wait_healthz(url: str, timeout: float = 90) -> int:
    deadline = time.monotonic() + timeout
    status = 0
    while time.monotonic() < deadline:
        try:
            status = httpx.get(f"{url}/healthz", timeout=5).status_code
        except httpx.HTTPError:
            status = 0
        if status == 200:
            return status
        time.sleep(1)
    return status


def test_the_image_runs_antifaz_and_serves_by_default(stack: Stack) -> None:
    template = "{{json .Config.Entrypoint}} {{json .Config.Cmd}}"
    config = docker("image", "inspect", "--format", template, IMAGE)
    assert config.stdout.strip() == '["antifaz"] ["serve"]'
    # docker-compose.yml sets no command (tests/unit/test_container_files.py), so the healthy
    # stack of test_compose.py already runs this default.


def test_help_works_in_the_image(stack: Stack) -> None:
    result = docker("run", "--rm", "--network", "none", IMAGE, "--help")
    assert result.returncode == 0, result.stderr
    for command in ("serve", "init", "verify", "doctor", "scan", "mask"):
        assert command in result.stdout, command


def test_serve_takes_no_options_in_the_image(stack: Stack) -> None:
    result = docker("run", "--rm", "--network", "none", IMAGE, "serve", "--port", "9000")
    assert result.returncode == 2


@pytest.fixture(scope="module")
def init_folder(stack: Stack, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A folder where `antifaz init` ran inside the image, removed at the end."""
    folder = tmp_path_factory.mktemp("init")
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture(scope="module")
def fake_keys() -> dict[str, str]:
    return {
        OPENAI_VAR: f"sk-e2e{secrets.token_hex(24)}",
        ANTHROPIC_VAR: f"sk-ant-e2e{secrets.token_hex(24)}",
    }


# The hardening of the README: no network (init never uses it), read-only image, no
# capabilities; only the mounted folder is writable. `-e NAME` (no value): docker takes the
# value from its own environment, so the key is never in an argument.
INIT_OPTIONS = ["--network", "none", "--read-only", "--cap-drop", "ALL"]
INIT_OPTIONS += ["--security-opt", "no-new-privileges", "-e", OPENAI_VAR, "-e", ANTHROPIC_VAR]
INIT_ARGS = ["init", "--non-interactive", "--openai-key-env", OPENAI_VAR]
INIT_ARGS += ["--anthropic-key-env", ANTHROPIC_VAR]


def _run_init(folder: Path, keys: dict[str, str]) -> tuple[int, str]:
    mount = ["-v", f"{folder}:/work", "-w", "/work", *_user()]
    result = subprocess.run(
        ["docker", "run", "--rm", *INIT_OPTIONS, *mount, IMAGE, *INIT_ARGS],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=os.environ | keys,
        check=False,
    )
    return result.returncode, result.stdout + result.stderr


@pytest.fixture(scope="module")
def env_file(init_folder: Path, fake_keys: dict[str, str]) -> Path:
    code, output = _run_init(init_folder, fake_keys)
    leaked = any(key in output for key in fake_keys.values())
    assert not leaked, "a provider key appeared in the output of init"
    assert code == 0, "init failed in the container (output not shown: it may hold paths)"
    env = init_folder / ".env"
    # Without -t the output is not a terminal: the new Antifaz key is not shown either.
    lines = env.read_text(encoding="utf-8").splitlines()
    (gateway,) = [
        x.removeprefix("ANTIFAZ_API_KEY=") for x in lines if x.startswith("ANTIFAZ_API_KEY=")
    ]
    shown = bool(gateway) and gateway in output
    assert not shown, "the new Antifaz key appeared in the output of init without a terminal"
    assert "ANTIFAZ_API_KEY" in output  # it says where the key is instead
    return env


def test_init_in_the_container_writes_a_usable_env(
    env_file: Path, fake_keys: dict[str, str]
) -> None:
    text = env_file.read_text(encoding="utf-8")
    lines = text.splitlines()
    has_openai = f"ANTIFAZ_OPENAI_API_KEY={fake_keys[OPENAI_VAR]}" in lines
    has_anthropic = f"ANTIFAZ_ANTHROPIC_API_KEY={fake_keys[ANTHROPIC_VAR]}" in lines
    assert has_openai and has_anthropic, "the provider keys are not in the .env written by init"
    (gateway,) = [x for x in lines if x.startswith("ANTIFAZ_API_KEY=")]
    assert len(gateway.removeprefix("ANTIFAZ_API_KEY=")) == 64
    if sys.platform != "win32":
        # Owned by the host user (--user), readable only by them.
        assert env_file.stat().st_uid == os.getuid()
        assert env_file.stat().st_mode & 0o777 == 0o600


def test_init_in_the_container_never_prints_the_new_key(
    init_folder: Path, env_file: Path, fake_keys: dict[str, str]
) -> None:
    # A second run (with --force it would replace .env): without it, code 2 and nothing changes.
    before = env_file.read_bytes()
    code, output = _run_init(init_folder, fake_keys)
    assert code == 2
    assert env_file.read_bytes() == before
    gateway = next(
        x for x in before.decode().splitlines() if x.startswith("ANTIFAZ_API_KEY=")
    ).removeprefix("ANTIFAZ_API_KEY=")
    leaked = gateway in output or any(key in output for key in fake_keys.values())
    assert not leaked, "a key appeared in the output of init"


def test_compose_starts_with_the_env_written_by_init(env_file: Path) -> None:
    port = _free_port()
    environ = {k: v for k, v in os.environ.items() if not k.startswith("ANTIFAZ_")}
    environ |= {"ANTIFAZ_ENV_FILE": str(env_file), "ANTIFAZ_PORT": str(port)}
    files = [arg for path in BASE_FILES for arg in ("-f", str(path))]
    base = ["docker", "compose", "-p", PROJECT, "--project-directory", str(REPO), *files]
    base += ["--env-file", str(env_file)]

    def compose(*args: str, timeout: float = 180) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*base, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environ,
            check=False,
        )

    compose("down", "--volumes", "--remove-orphans")
    try:
        up = compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "120", timeout=300)
        assert up.returncode == 0, "docker compose up failed with the .env written by init"
        assert _wait_healthz(f"http://127.0.0.1:{port}") == 200
        refused = httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=10)
        assert refused.status_code == 401
    finally:
        compose("down", "--volumes", "--remove-orphans")


def test_the_hardened_one_line_docker_run_starts_with_that_env(env_file: Path) -> None:
    port = _free_port()
    name = f"antifaz-e2e-run-{secrets.token_hex(4)}"
    # The same command as the README, with the local image and a free port.
    command = [
        "run", "-d", "--name", name, "--env-file", str(env_file),
        "-p", f"127.0.0.1:{port}:8000",
        "--read-only", "--tmpfs", "/tmp:size=16m,mode=1777,noexec,nosuid,nodev",  # noqa: S108
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--memory", "512m", "--cpus", "1", "--pids-limit", "128",
        IMAGE,
    ]  # fmt: skip
    try:
        started = docker(*command)
        assert started.returncode == 0, started.stderr
        assert _wait_healthz(f"http://127.0.0.1:{port}") == 200
        user = docker("exec", name, "id", "-u").stdout.strip()
        assert user == "10001"
    finally:
        docker("rm", "-f", name)
