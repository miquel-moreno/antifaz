"""End-to-end stack (issue 7a): the real image under Docker Compose with a fake provider.

`make e2e` builds the image with the repository's docker-compose.yml, compose.build.yml (the
local build) and tests/e2e/docker-compose.e2e.yml, starts it with a throwaway env file (random
keys made here, base URLs pointing at the fake provider) and stops and removes everything at
the end. Your .env is never read: the env file is passed both as --env-file and as
ANTIFAZ_ENV_FILE. Keys are never printed: assertions about them compare booleans with a fixed
message.
"""

import os
import secrets
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[2]
PROJECT = "antifaz-e2e"
IMAGE = "antifaz:local"
# The published-image file, the local build on top (antifaz:local) and the fake provider.
BASE_FILES = (REPO / "docker-compose.yml", REPO / "compose.build.yml")
COMPOSE_FILES = (*BASE_FILES, REPO / "tests" / "e2e" / "docker-compose.e2e.yml")

# Filled by the `image_size` test, printed at the end of the run.
SUMMARY: list[str] = []


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def docker(*args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    """Run the docker CLI (fixed argument list, never a shell) and capture its output."""
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


@dataclass
class Stack:
    gateway: str
    fake: str
    gateway_key: str
    openai_key: str
    anthropic_key: str
    env_file: Path
    environ: dict[str, str] = field(repr=False)

    def compose(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
        files = [arg for path in COMPOSE_FILES for arg in ("-f", str(path))]
        command = ["docker", "compose", "-p", PROJECT, "--project-directory", str(REPO)]
        return subprocess.run(
            [*command, *files, "--env-file", str(self.env_file), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=self.environ,
            check=False,
        )

    def container(self, service: str) -> str:
        return self.compose("ps", "-q", service).stdout.strip()

    def received(self) -> list[dict[str, str | None]]:
        """Every request the fake provider got, as it got it."""
        answer = httpx.get(f"{self.fake}/_e2e/received", timeout=10)
        answer.raise_for_status()
        data: list[dict[str, str | None]] = answer.json()
        return data


def _wait_healthy(stack: Stack, timeout: float = 90) -> str:
    deadline = time.monotonic() + timeout
    status = ""
    while time.monotonic() < deadline:
        container = stack.container("antifaz")
        status = docker("inspect", "--format", "{{.State.Health.Status}}", container).stdout
        if status.strip() == "healthy":
            return "healthy"
        time.sleep(1)
    return status.strip()


@pytest.fixture(scope="session")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    if docker("version").returncode != 0:
        pytest.fail("Docker is not running: start Docker and run `make e2e` again")
    env_file = tmp_path_factory.mktemp("e2e") / "e2e.env"
    gateway_port, fake_port = _free_port(), _free_port()
    keys = {name: secrets.token_hex(32) for name in ("gateway", "openai", "anthropic")}
    env_file.write_text(
        "\n".join(
            [
                f"ANTIFAZ_API_KEY={keys['gateway']}",
                f"ANTIFAZ_OPENAI_API_KEY={keys['openai']}",
                f"ANTIFAZ_ANTHROPIC_API_KEY={keys['anthropic']}",
                # The fake provider on the Compose network. Plain http on purpose: base URLs
                # are admin configuration, and here they never leave the private network.
                "ANTIFAZ_OPENAI_BASE_URL=http://fake-upstream:9000/v1",
                "ANTIFAZ_ANTHROPIC_BASE_URL=http://fake-upstream:9000",
                "ANTIFAZ_ALLOWED_HOSTS=localhost,127.0.0.1,antifaz",
                "ANTIFAZ_LOG_LEVEL=INFO",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    # Nothing ANTIFAZ_* from your shell reaches Compose: only what this run sets.
    environ = {k: v for k, v in os.environ.items() if not k.startswith("ANTIFAZ_")}
    environ |= {
        "ANTIFAZ_ENV_FILE": str(env_file),
        "ANTIFAZ_PORT": str(gateway_port),
        "ANTIFAZ_E2E_FAKE_PORT": str(fake_port),
    }
    stack = Stack(
        gateway=f"http://127.0.0.1:{gateway_port}",
        fake=f"http://127.0.0.1:{fake_port}",
        gateway_key=keys["gateway"],
        openai_key=keys["openai"],
        anthropic_key=keys["anthropic"],
        env_file=env_file,
        environ=environ,
    )
    stack.compose("down", "--volumes", "--remove-orphans")  # leftovers of an aborted run
    try:
        built = stack.compose("build", timeout=900)
        assert built.returncode == 0, f"docker compose build failed:\n{built.stderr[-4000:]}"
        up = stack.compose("up", "-d", "--wait", "--wait-timeout", "120", timeout=300)
        if up.returncode != 0:
            logs = stack.compose("logs", "--no-color").stdout
            pytest.fail(f"docker compose up failed:\n{up.stderr[-2000:]}\n{logs[-4000:]}")
        yield stack
    finally:
        stack.compose("down", "--volumes", "--remove-orphans", timeout=180)
        env_file.unlink(missing_ok=True)


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    for line in SUMMARY:
        terminalreporter.write_line(line)
