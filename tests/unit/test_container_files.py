"""Static checks of the Dockerfile, .dockerignore and docker-compose.yml (issue 7a).

They run in `make check` without Docker; `make e2e` checks the same things on the running
container. If one fails, the hardening of the image or of Compose was weakened.
"""

import re
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = (REPO / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
COMPOSE: dict[str, Any] = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
SERVICE: dict[str, Any] = COMPOSE["services"]["antifaz"]


def _instructions(name: str) -> list[str]:
    """The arguments of every `NAME ...` instruction (continuation lines joined)."""
    joined = re.sub(r"\\\n\s*", " ", DOCKERFILE)
    return [m.group(1).strip() for m in re.finditer(rf"^{name}\s+(.+)$", joined, re.MULTILINE)]


def test_every_base_image_is_pinned_by_digest() -> None:
    images = [line.split()[0] for line in _instructions("FROM")]
    assert images, "no FROM line"
    for image in images:
        assert re.fullmatch(r"[a-z0-9./_-]+:[\w.-]+@sha256:[0-9a-f]{64}", image), image


def test_build_and_final_stages_use_the_same_python_image() -> None:
    pythons = {line.split()[0] for line in _instructions("FROM") if line.startswith("python:")}
    assert len(pythons) == 1


def test_the_final_stage_runs_as_a_fixed_non_root_user() -> None:
    users = _instructions("USER")
    assert users and users[-1] == "10001:10001"


def test_the_image_has_a_healthcheck_without_curl() -> None:
    (healthcheck,) = _instructions("HEALTHCHECK")
    assert "antifaz.healthcheck" in healthcheck
    assert "curl" not in healthcheck
    # Nothing is installed with the system package manager (no curl, wget or shells added).
    assert not any("apt-get" in line or "apk " in line for line in _instructions("RUN"))


def test_the_server_starts_from_the_factory_without_proxy_headers() -> None:
    (entrypoint,) = _instructions("ENTRYPOINT")
    assert '"--factory", "antifaz.api.app:create_app"' in entrypoint
    assert '"--no-proxy-headers"' in entrypoint


def test_the_image_installs_no_dev_dependencies_and_no_extras() -> None:
    syncs = [line for line in _instructions("RUN") if "uv sync" in line]
    assert syncs
    for line in syncs:
        assert "--frozen" in line and "--no-dev" in line
        assert "--extra" not in line and "--all-extras" not in line


def test_no_secret_is_set_in_the_dockerfile() -> None:
    for line in _instructions("ENV") + _instructions("ARG"):
        assert not re.search(r"KEY|SECRET|TOKEN|PASSWORD", line, re.IGNORECASE), line


def test_dockerignore_is_an_allowlist_that_keeps_env_files_out() -> None:
    rules = [r.strip() for r in DOCKERIGNORE if r.strip() and not r.startswith("#")]
    assert rules[0] == "*"
    allowed = {r for r in rules if r.startswith("!")}
    assert allowed == {"!pyproject.toml", "!uv.lock", "!README.md", "!LICENSE", "!NOTICE", "!src/"}
    assert "**/.env" in rules and "**/.env.*" in rules


def test_compose_reads_keys_from_the_env_file_and_never_holds_them() -> None:
    assert SERVICE["env_file"][0]["path"] == "${ANTIFAZ_ENV_FILE:-.env}"
    for name in SERVICE.get("environment", {}):
        assert not re.search(r"KEY|SECRET|TOKEN", name), name


def test_compose_hardens_the_container() -> None:
    assert SERVICE["read_only"] is True
    assert SERVICE["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in SERVICE["security_opt"]
    assert any(t.startswith("/tmp:") and "noexec" in t for t in SERVICE["tmpfs"])  # noqa: S108
    assert SERVICE["mem_limit"] and SERVICE["cpus"] and SERVICE["pids_limit"]


def test_compose_publishes_the_port_on_loopback_only() -> None:
    assert SERVICE["ports"] == ["127.0.0.1:${ANTIFAZ_PORT:-8000}:8000"]


def test_compose_allows_the_service_name_as_host() -> None:
    hosts = SERVICE["environment"]["ANTIFAZ_ALLOWED_HOSTS"]
    assert hosts.startswith("${ANTIFAZ_ALLOWED_HOSTS:-")
    default = hosts.removeprefix("${ANTIFAZ_ALLOWED_HOSTS:-").removesuffix("}").split(",")
    assert "antifaz" in default and "localhost" in default
