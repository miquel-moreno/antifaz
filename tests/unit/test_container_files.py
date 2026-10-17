"""Static checks of the Dockerfile, .dockerignore, docker-compose.yml and compose.build.yml
(issues 7a and 42).

They run in `make check` without Docker; `make e2e` checks the same things on the running
container. If one fails, the hardening of the image or of Compose was weakened.
"""

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = (REPO / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
COMPOSE: dict[str, Any] = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
CI: dict[str, Any] = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
DEPENDABOT: dict[str, Any] = yaml.safe_load(
    (REPO / ".github/dependabot.yml").read_text(encoding="utf-8")
)
BUILD_OVERRIDE: dict[str, Any] = yaml.safe_load(
    (REPO / "compose.build.yml").read_text(encoding="utf-8")
)
MAKEFILE = (REPO / "Makefile").read_text(encoding="utf-8")
SERVICE: dict[str, Any] = COMPOSE["services"]["antifaz"]
VERSION: str = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"][
    "version"
]


def _instructions(name: str) -> list[str]:
    """The arguments of every `NAME ...` instruction (continuation lines joined)."""
    joined = re.sub(r"\\\n\s*", " ", DOCKERFILE)
    return [m.group(1).strip() for m in re.finditer(rf"^{name}\s+(.+)$", joined, re.MULTILINE)]


def test_every_base_image_is_pinned_by_digest() -> None:
    images = [line.split()[0] for line in _instructions("FROM")]
    assert images, "no FROM line"
    for image in images:
        assert re.fullmatch(r"[a-z0-9./_-]+:[\w.-]+@sha256:[0-9a-f]{64}", image), image


def _stages() -> dict[str, str]:
    """{stage name: base image} from every `FROM image AS name` line."""
    stages = {}
    for line in _instructions("FROM"):
        image, as_, name = line.split()
        assert as_ == "AS", line
        stages[name] = image
    return stages


def _stage(name: str) -> str:
    """The text of one stage (continuation lines joined), from its FROM to the next FROM."""
    joined = re.sub(r"\\\n\s*", " ", DOCKERFILE)
    after = re.split(rf"^FROM\s+\S+\s+AS\s+{name}\s*$", joined, flags=re.MULTILINE)[1]
    return re.split(r"^FROM\s", after, flags=re.MULTILINE)[0]


# Issue 54 (ADR-0019): the build and the Python runtime come from the official Python image of
# Debian 13 "trixie", the final stage is distroless cc on Debian 13. The tag names the Debian
# release, so a Dependabot PR that moves only one of them to another Debian fails here.
PYTHON_IMAGE = re.compile(r"python:3\.12-slim-trixie@sha256:[0-9a-f]{64}")
DISTROLESS_IMAGE = re.compile(r"gcr\.io/distroless/cc-debian13:nonroot@sha256:[0-9a-f]{64}")


def test_build_and_python_stages_use_the_same_python_image() -> None:
    stages = _stages()
    pythons = {image for image in stages.values() if image.startswith("python:")}
    assert len(pythons) == 1
    assert stages["build"] == stages["python"]
    assert PYTHON_IMAGE.fullmatch(stages["build"]), stages["build"]


def test_the_final_stage_is_distroless_cc_on_the_same_debian_release() -> None:
    stages = _stages()
    assert list(stages)[-1] == "runtime"
    assert DISTROLESS_IMAGE.fullmatch(stages["runtime"]), stages["runtime"]
    # trixie is Debian 13: the Python copied in was built for the C library of the final base.
    assert "-trixie@" in stages["python"] and "-debian13:" in stages["runtime"]


def test_the_final_stage_runs_nothing() -> None:
    # Distroless has no shell: a RUN there could not work, and nothing is installed or changed.
    assert re.findall(r"^RUN\s", _stage("runtime"), re.MULTILINE) == []


def test_the_final_stage_copies_only_python_and_the_virtualenv() -> None:
    copies = re.findall(r"^COPY\s+(.+)$", _stage("runtime"), re.MULTILINE)
    assert [c.strip() for c in copies] == [
        "--from=python /usr/local/bin/python3.12 /usr/local/bin/python3.12",
        "--from=python /usr/local/lib/libpython3.12.so.1.0 /usr/local/lib/libpython3.12.so.1.0",
        "--from=python /usr/local/lib/python3.12 /usr/local/lib/python3.12",
        "--from=build /app/.venv /app/.venv",
    ]


def test_the_python_stage_drops_installers_tools_and_modules_without_their_library() -> None:
    joined = " ".join(re.findall(r"^RUN\s+(.+)$", _stage("python"), re.MULTILINE))
    for leftover in ("site-packages/pip", "site-packages/setuptools", "site-packages/wheel"):
        assert leftover in joined, leftover
    for folder in ("ensurepip", "idlelib", "tkinter", "turtledemo", "ctypes", "sqlite3"):
        assert f" {folder} " in f" {joined} ", folder
    # The compiled modules whose system library distroless cc does not ship (ADR-0019; the e2e
    # test checks that nothing installed needs them), removed one by one from lib-dynload.
    assert 'rm "lib-dynload/${module}.cpython-312-x86_64-linux-gnu.so"' in joined
    for module in ("_ctypes", "_sqlite3", "_lzma", "_bz2", "readline", "_curses", "_dbm"):
        assert f" {module} " in joined, module
    for module in ("_gdbm", "_uuid", "_crypt", "_tkinter", "_testcapi"):
        assert f" {module} " in joined, module


def test_the_final_stage_declares_the_ca_bundle_it_relies_on() -> None:
    # Distroless sets SSL_CERT_FILE itself today; declared here so a new base cannot drop it
    # silently (ssl.create_default_context() would then trust nothing).
    env = " ".join(re.findall(r"^ENV\s+(.+)$", _stage("runtime"), re.MULTILINE))
    assert "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt" in env.split()


def test_the_final_stage_runs_as_a_fixed_non_root_user() -> None:
    users = _instructions("USER")
    assert users and users[-1] == "10001:10001"


def test_the_image_has_a_healthcheck_without_curl_or_a_shell() -> None:
    (healthcheck,) = _instructions("HEALTHCHECK")
    assert "antifaz.healthcheck" in healthcheck
    assert "curl" not in healthcheck
    # Exec form: there is no shell in the image to run a CMD-SHELL healthcheck.
    assert '["python", "-m", "antifaz.healthcheck"]' in healthcheck
    assert "CMD-SHELL" not in DOCKERFILE
    # Nothing is installed with the system package manager (no curl, wget or shells added).
    for line in _instructions("RUN"):
        assert "apt-get" not in line and "apk " not in line, line


def test_the_image_runs_the_antifaz_command_and_serves_by_default() -> None:
    # ADR-0017: one command in the image, so the same image runs `init`, `verify` and `serve`.
    # The uvicorn settings of `serve` are pinned in tests/unit/test_cli_serve.py.
    assert _instructions("ENTRYPOINT") == ['["antifaz"]']
    assert _instructions("CMD") == ['["serve"]']
    final = DOCKERFILE.rsplit("AS runtime", 1)[1]
    assert final.index("USER 10001:10001") < final.index("ENTRYPOINT")
    assert "uvicorn" not in " ".join(_instructions("ENTRYPOINT") + _instructions("CMD"))


def test_the_image_installs_no_dev_dependencies_and_no_extras() -> None:
    syncs = [line for line in _instructions("RUN") if "uv sync" in line]
    assert syncs
    for line in syncs:
        assert "--frozen" in line and "--no-dev" in line
        assert "--extra" not in line and "--all-extras" not in line
        # No dependency group either: `bench` and `panel` (Playwright, PR 11c-2a) never ship.
        assert "--group" not in line and "--all-groups" not in line and "--only-group" not in line


def test_no_secret_is_set_in_the_dockerfile() -> None:
    for line in _instructions("ENV") + _instructions("ARG"):
        assert not re.search(r"KEY|SECRET|TOKEN|PASSWORD", line, re.IGNORECASE), line


def test_dockerignore_is_an_allowlist_that_keeps_env_files_out() -> None:
    rules = [r.strip() for r in DOCKERIGNORE if r.strip() and not r.startswith("#")]
    assert rules[0] == "*"
    allowed = {r for r in rules if r.startswith("!")}
    assert allowed == {"!pyproject.toml", "!uv.lock", "!README.md", "!LICENSE", "!NOTICE", "!src/"}
    # The allowlist already keeps these out; the explicit rules also cover the allowed folders.
    for never in ("**/.env", "**/.env.*", "**/models", "**/evals", "**/.git", "**/.cache"):
        assert never in rules, never


def test_compose_runs_the_published_image_of_this_version() -> None:
    # ADR-0017: users run the published image, not a local build. The default is the version
    # in pyproject.toml: on main it is the last release, and the release commit bumps both.
    assert SERVICE["image"] == f"ghcr.io/miquel-moreno/antifaz:${{ANTIFAZ_VERSION:-{VERSION}}}"
    assert "build" not in SERVICE


def test_compose_keeps_the_image_command() -> None:
    # No `command:` or `entrypoint:`: the image's own (`antifaz serve`) runs, and the same file
    # also works with images up to 0.1.0, whose entrypoint was uvicorn itself.
    assert "command" not in SERVICE and "entrypoint" not in SERVICE


def test_the_local_build_lives_in_its_own_override() -> None:
    assert BUILD_OVERRIDE == {
        "services": {"antifaz": {"build": {"context": "."}, "image": "antifaz:local"}}
    }


def test_the_makefile_and_ci_build_the_same_local_image() -> None:
    assert "-f docker-compose.yml -f compose.build.yml" in MAKEFILE
    (build,) = [s for s in _image_steps() if "docker buildx build" in str(s.get("run", ""))]
    # The same context (.) and tag as compose.build.yml, so `make e2e` reuses the cached image.
    assert build["run"].split() == [
        "docker",
        "buildx",
        "build",
        "--load",
        "--tag",
        "antifaz:local",
        ".",
    ]


def test_the_e2e_tests_use_the_build_override() -> None:
    conftest = (REPO / "tests/e2e/conftest.py").read_text(encoding="utf-8")
    assert '"compose.build.yml"' in conftest


def _verbosity(args: list[str]) -> int:
    count = 0
    for arg in args:
        if re.fullmatch(r"-v+", arg):
            count += len(arg) - 1
        elif arg == "--verbose":
            count += 1
        elif re.fullmatch(r"-q+", arg):
            count -= len(arg) - 1
        elif arg == "--quiet":
            count -= 1
    return count


def test_make_e2e_names_every_test_in_the_log() -> None:
    """Issue 54: CI logs list each e2e test. pyproject's addopts count too (-q cancels a -v)."""
    (recipe,) = re.findall(r"^e2e:\n\t(.+)$", MAKEFILE, re.MULTILINE)
    pytest_ini = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = pytest_ini["tool"]["pytest"]["ini_options"].get("addopts", "").split()
    assert _verbosity(addopts + recipe.split()) >= 1


def test_the_verbosity_count_follows_pytest() -> None:
    assert _verbosity(["-q", "-v"]) == 0
    assert _verbosity(["-q", "-vv"]) == 1
    assert _verbosity(["--quiet", "--verbose", "-v"]) == 1
    assert _verbosity(["-qq", "-v"]) == -1


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


def test_the_env_file_is_the_only_source_of_settings() -> None:
    # No `environment:` that could silently override what the admin wrote in .env.
    assert "environment" not in SERVICE


def test_the_example_env_file_allows_the_service_name_as_host() -> None:
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    (line,) = [x for x in example.splitlines() if x.startswith("ANTIFAZ_ALLOWED_HOSTS=")]
    hosts = line.removeprefix("ANTIFAZ_ALLOWED_HOSTS=").split(",")
    assert "antifaz" in hosts and "localhost" in hosts


def _image_steps() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = CI["jobs"]["image"]["steps"]
    return steps


def _grype_steps() -> list[dict[str, Any]]:
    return [s for s in _image_steps() if str(s.get("uses", "")).startswith("anchore/scan-action@")]


def test_grype_gate_fails_only_on_high_or_critical_with_a_fix() -> None:
    gates = [s for s in _grype_steps() if s["with"].get("fail-build") is True]
    assert len(gates) == 1
    (gate,) = gates
    assert gate["with"]["severity-cutoff"] == "high"
    assert gate["with"]["only-fixed"] is True
    assert gate["with"]["config"] == ".github/grype-gate.yaml"
    assert (REPO / ".github/grype-gate.yaml").is_file()


def test_grype_full_report_lists_everything_and_never_fails() -> None:
    # The same full report twice: a table to read and JSON for check_scan_sees (issue 54).
    reports = [s for s in _grype_steps() if s["with"].get("fail-build") is False]
    assert [(r["id"], r["with"]["output-format"], r["with"]["output-file"]) for r in reports] == [
        ("grype-report", "table", "grype-report.txt"),
        ("grype-json", "json", "grype-report.json"),
    ]
    for report in reports:
        assert report["with"]["only-fixed"] is False
        assert "config" not in report["with"]  # the gate's ignore rules hide nothing here
        assert report["with"]["image"] == "antifaz:local"


def test_ci_fails_if_the_scanners_stop_seeing_the_system_packages() -> None:
    """Issue 54: on distroless, Debian packages are only visible through status.d records."""
    steps = _image_steps()
    (sbom,) = [s for s in steps if str(s.get("uses", "")).startswith("anchore/sbom-action@")]
    assert sbom["with"]["output-file"] == "antifaz-image.cdx.json"
    (check,) = [s for s in steps if "scripts.check_scan_sees" in str(s.get("run", ""))]
    # Miquel: the structure blocks (Debian 13, deb matches, libc6/libssl3t64/zlib1g, CPython),
    # the known CVE only warns.
    assert check["run"].split() == [
        "python3", "-m", "scripts.check_scan_sees",
        "--sbom", "antifaz-image.cdx.json", "--grype", "grype-report.json",
        "--distro", "debian", "--distro-version", "13",
        "--deb", "libc6", "--deb", "libssl3t64", "--deb", "zlib1g", "--python", "3.12",
        "--warn-vulnerability", "CVE-2026-85091", "--warn-package", "zlib1g",
    ]  # fmt: skip
    assert "continue-on-error" not in check
    json_report = next(s for s in steps if s.get("id") == "grype-json")
    gate = next(s for s in _grype_steps() if s["with"].get("fail-build") is True)
    assert steps.index(sbom) < steps.index(json_report) < steps.index(check) < steps.index(gate)


def test_grype_full_report_is_uploaded_even_when_the_gate_fails() -> None:
    steps = _image_steps()
    (upload,) = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert re.fullmatch(r"actions/upload-artifact@[0-9a-f]{40}", upload["uses"])
    assert "always()" in upload["if"] and "steps.grype-report.outcome" in upload["if"]
    assert upload["with"]["path"].split() == ["grype-report.txt", "grype-report.json"]
    assert upload["with"]["retention-days"] == 30
    # After the gate, so a failing gate still uploads the report.
    gate = next(s for s in _grype_steps() if s["with"].get("fail-build") is True)
    assert steps.index(upload) > steps.index(gate)


def test_dependabot_updates_the_base_image_digests_with_a_cooldown() -> None:
    (docker,) = [u for u in DEPENDABOT["updates"] if u["package-ecosystem"] == "docker"]
    assert docker["directory"] == "/"
    assert docker["schedule"]["interval"] == "weekly"
    assert docker["cooldown"]["default-days"] == 7


def test_the_same_uv_version_everywhere() -> None:
    """The image, CI and `make audit` use one uv version: a bump changes all three at once."""
    image = re.findall(r"^FROM ghcr\.io/astral-sh/uv:(\d+\.\d+\.\d+)@", DOCKERFILE, re.MULTILINE)
    ci = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    audit = re.findall(r"uvx --from uv==(\d+\.\d+\.\d+) uv audit", makefile)

    assert len(image) == 1
    assert len(audit) == 1
    assert image[0] == ci["env"]["UV_VERSION"] == audit[0]
