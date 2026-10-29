"""`make test-postgres`: the tests with a real, throwaway PostgreSQL 16 in Docker (PR 8b-1).

Runs the SAME image as CI (read from the `quality` job's service in .github/workflows/ci.yml,
pinned by digest) with `docker run --rm`, published on 127.0.0.1 only and on a free port Docker
chooses, with scram-sha-256 and a random admin password made here (passed to Docker through the
environment, never in argv). Waits for pg_isready (60 s at most), then runs the tests with
--postgres and the TEST_POSTGRES_* variables, and always stops the container (it removes
itself). No testcontainers, no network but the image pull.

    uv run python -m scripts.postgres_tests              # like `make test POSTGRES=1`
    uv run python -m scripts.postgres_tests tests/postgres -x   # only those, no coverage
"""

import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
_IMAGE = re.compile(r"^\s*image:\s*(postgres:16@sha256:[0-9a-f]{64})\s*(?:#.*)?$", re.MULTILINE)
ADMIN = "antifaz_admin"
MAINTENANCE_DATABASE = "antifaz_test_admin"
READY_SECONDS = 60


def ci_image() -> str:
    """The PostgreSQL image of CI's `quality` job (the only `postgres:16@sha256:` in ci.yml)."""
    found = _IMAGE.findall(CI.read_text(encoding="utf-8"))
    if len(found) != 1:
        raise SystemExit("ci.yml must name exactly one postgres:16@sha256:... image")
    image: str = found[0]
    return image


def _docker(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    docker = shutil.which("docker")
    if docker is None:
        raise SystemExit("make test-postgres needs Docker")
    return subprocess.run(  # noqa: S603 - fixed program and arguments, never a shell
        [docker, *args], capture_output=True, text=True, env=env, check=False, timeout=600
    )


def _docker_ok(*args: str, env: dict[str, str] | None = None) -> str:
    result = _docker(*args, env=env)
    if result.returncode != 0:
        raise SystemExit(f"docker {args[0]} failed (exit {result.returncode})")
    return result.stdout.strip()


def _wait_until_ready(name: str) -> None:
    """pg_isready over TCP: the temporary server of the image's first start has no TCP port."""
    probe = ("exec", name, "pg_isready", "-q", "-h", "127.0.0.1", "-U", ADMIN)
    deadline = time.monotonic() + READY_SECONDS
    while time.monotonic() < deadline:
        if _docker(*probe, "-d", MAINTENANCE_DATABASE).returncode == 0:
            return
        time.sleep(0.5)
    raise SystemExit(f"PostgreSQL was not ready within {READY_SECONDS} s")


def main(argv: Sequence[str]) -> int:
    image = ci_image()
    password = secrets.token_hex(24)
    name = f"antifaz-test-postgres-{secrets.token_hex(4)}"
    docker_env = dict(os.environ, POSTGRES_PASSWORD=password)
    _docker_ok(
        "run",
        "--rm",
        "-d",
        "--name",
        name,
        "-e",
        "POSTGRES_PASSWORD",  # the value comes from the environment, not argv
        "-e",
        f"POSTGRES_USER={ADMIN}",
        "-e",
        f"POSTGRES_DB={MAINTENANCE_DATABASE}",
        "-e",
        "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256 --auth-local=scram-sha-256",
        "-p",
        "127.0.0.1::5432",
        image,
        env=docker_env,
    )
    try:
        published = _docker_ok("port", name, "5432/tcp").splitlines()[0]
        host, _, port = published.rpartition(":")
        if host != "127.0.0.1":
            raise SystemExit("the test PostgreSQL must be published on 127.0.0.1 only")
        _wait_until_ready(name)
        env = {
            name_: value
            for name_, value in os.environ.items()
            if not name_.upper().startswith(("ANTIFAZ_", "PG"))
        }
        env |= {
            "TEST_POSTGRES_HOST": host,
            "TEST_POSTGRES_PORT": port,
            "TEST_POSTGRES_USER": ADMIN,
            "TEST_POSTGRES_PASSWORD": password,
            "TEST_POSTGRES_DATABASE": MAINTENANCE_DATABASE,
        }
        uv = shutil.which("uv") or "uv"
        if argv:
            command = [uv, "run", "pytest", "--postgres", "--no-cov", *argv]
            return subprocess.run(command, env=env, check=False).returncode  # noqa: S603
        tests = [
            uv,
            "run",
            "pytest",
            "--postgres",
            "--cov",
            "--cov-report=term-missing",
            "--cov-report=json",
        ]
        code = subprocess.run(tests, env=env, check=False).returncode  # noqa: S603
        if code != 0:
            return code
        coverage = [uv, "run", "python", "-m", "scripts.check_coverage"]
        return subprocess.run(coverage, env=env, check=False).returncode  # noqa: S603
    finally:
        _docker("stop", "--time", "5", name)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
