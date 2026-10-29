"""The real PostgreSQL of CI and of `make test-postgres` (issue 121, PR 8b-1, decision D9).

The `quality` job runs a `postgres:16` service pinned by digest, published on 127.0.0.1 only,
with scram-sha-256 (never trust) and a pg_isready health check; its test step passes --postgres
and gives the tests TEST_POSTGRES_* variables (never ANTIFAZ_*), and its coverage step checks the
db piece too. Dependabot's github-actions ecosystem does not update `services:` images: the
digest is refreshed by hand (docs/TECNICO.md) until 8f. `make test-postgres` runs the same
digest, read from ci.yml.
"""

import re
from pathlib import Path
from typing import Any

import yaml
from scripts import postgres_tests

REPO = Path(__file__).resolve().parents[2]
CI: dict[str, Any] = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
QUALITY = CI["jobs"]["quality"]
DIGEST_IMAGE = re.compile(r"postgres:16@sha256:[0-9a-f]{64}")


def service() -> dict[str, Any]:
    services = QUALITY.get("services", {})
    assert set(services) == {"postgres"}
    value: dict[str, Any] = services["postgres"]
    return value


def step(name: str) -> dict[str, Any]:
    (found,) = [s for s in QUALITY["steps"] if s.get("name") == name]
    return found


def test_the_service_is_postgres_16_pinned_by_digest() -> None:
    assert DIGEST_IMAGE.fullmatch(service()["image"])


def test_the_service_is_published_on_loopback_only() -> None:
    ports = service()["ports"]

    assert ports
    assert all(str(port).startswith("127.0.0.1:") for port in ports)


def test_the_service_uses_scram_and_never_trust() -> None:
    env = service()["env"]

    assert "POSTGRES_HOST_AUTH_METHOD" not in env
    assert "--auth-host=scram-sha-256" in env["POSTGRES_INITDB_ARGS"]
    assert "--auth-local=scram-sha-256" in env["POSTGRES_INITDB_ARGS"]
    assert env["POSTGRES_PASSWORD"]  # a throwaway value that only exists in CI


def test_the_service_has_a_pg_isready_health_check() -> None:
    options = service()["options"]

    assert "--health-cmd" in options
    assert "pg_isready" in options


def test_the_quality_job_only_reads_the_code() -> None:
    assert QUALITY["permissions"] == {"contents": "read"}


def test_the_tests_run_against_the_service() -> None:
    tests = step("Tests")
    env = tests["env"]

    assert "--postgres" in tests["run"]
    assert set(env) == {
        "TEST_POSTGRES_HOST",
        "TEST_POSTGRES_PORT",
        "TEST_POSTGRES_USER",
        "TEST_POSTGRES_PASSWORD",
        "TEST_POSTGRES_DATABASE",
    }
    assert env["TEST_POSTGRES_HOST"] == "127.0.0.1"
    assert env["TEST_POSTGRES_PASSWORD"] == service()["env"]["POSTGRES_PASSWORD"]
    assert not any(name.startswith("ANTIFAZ_") for name in env)


def test_ci_checks_the_coverage_of_the_db_piece() -> None:
    assert "--without" not in step("Coverage >= 90 % in the privacy pieces")["run"]


def test_the_windows_job_has_no_database() -> None:
    windows = CI["jobs"]["cli-windows"]

    assert "services" not in windows
    assert "--postgres" not in str(windows)


def test_the_windows_job_runs_the_database_tests_that_need_no_postgresql() -> None:
    """`antifaz db` and its errors without a database behave the same on Windows (8b-1): the
    CLI job runs test_cli_db.py and every tests/unit/test_db_*.py (none needs --postgres)."""
    run = next(s for s in CI["jobs"]["cli-windows"]["steps"] if s.get("name") == "CLI tests")["run"]
    listed = set(run.split())
    wanted = {"tests/unit/test_cli_db.py"} | {
        path.relative_to(REPO).as_posix() for path in (REPO / "tests" / "unit").glob("test_db_*.py")
    }

    assert len(wanted) >= 6
    assert wanted <= listed, sorted(wanted - listed)
    assert "--postgres" not in listed


def test_make_test_postgres_runs_the_ci_image() -> None:
    assert postgres_tests.ci_image() == service()["image"]


def test_make_test_skips_the_db_coverage_unless_postgres_is_asked() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("\ntest:\n", 1)[1].split("\n\n", 1)[0]

    assert "$(if $(POSTGRES),--postgres,)" in recipe
    assert "$(if $(POSTGRES),,--without db)" in recipe
    assert "\ntest-postgres:\n" in makefile
