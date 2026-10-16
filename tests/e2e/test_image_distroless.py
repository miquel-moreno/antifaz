"""The distroless runtime image (issue 54, PR B2, ADR-0019). Only `make e2e`.

The final stage is gcr.io/distroless/cc-debian13 with CPython 3.12 copied from the official
python:3.12-slim-trixie image: no shell, no package manager, Debian 13 like the build. The
scanners (Syft, Grype) must still see every system package, so every shared library of the
image is either in a dpkg record of /var/lib/dpkg/status.d (the distroless format), part of
CPython or part of a package of the virtualenv. Standard-library modules whose system library
is not in the base were removed; these tests check that they are gone for good and that every
compiled module left imports.
"""

import os
import secrets

import pytest

from tests.e2e.conftest import IMAGE, Stack, docker
from tests.e2e.image_probes import (
    ABSENT_SHELLS,
    CA_CERTIFICATES,
    DEBIAN_VERSION,
    IMPORT_LIB_DYNLOAD,
    NO_USER_LOOKUP,
    NOT_MISSING,
    REMOVED_STDLIB,
    STATUS_D,
    UNACCOUNTED_LIBRARIES,
    python_in_image,
)

pytestmark = pytest.mark.e2e

# Packages the distroless cc base must list for the scanners: zlib (CPython links it; Debian has
# no fix for CVE-2026-85091 today), OpenSSL and the C library.
BASE_PACKAGES = ("zlib1g", "libssl3t64", "libc6")


@pytest.mark.parametrize("shell", ABSENT_SHELLS)
def test_the_image_has_no_shell_to_run(stack: Stack, shell: str) -> None:
    result = docker("run", "--rm", "--network", "none", "--entrypoint", shell, IMAGE, "-c", "true")
    assert result.returncode != 0, f"{shell} runs in the image"
    assert "executable file not found" in result.stderr, result.stderr[-500:]


def test_the_image_is_debian_13_like_the_build_stage(stack: Stack) -> None:
    result = python_in_image(DEBIAN_VERSION)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("13"), result.stdout


def test_the_default_tls_context_trusts_the_ca_bundle(stack: Stack) -> None:
    result = python_in_image(CA_CERTIFICATES)
    assert result.returncode == 0, result.stderr[-500:]
    assert int(result.stdout.strip()) > 0


def test_nothing_looks_up_the_user_without_a_passwd_entry_at_startup(stack: Stack) -> None:
    key = secrets.token_hex(32)
    environ = {k: v for k, v in os.environ.items() if not k.startswith("ANTIFAZ_")}
    environ["ANTIFAZ_API_KEY"] = key
    result = python_in_image(NO_USER_LOOKUP, options=("-e", "ANTIFAZ_API_KEY"), env=environ)
    assert key not in result.stdout + result.stderr, "the throwaway key appeared in the output"
    assert result.returncode == 0, result.stderr[-1000:]
    assert result.stdout.strip() == "", f"user lookups at startup: {result.stdout}"


def test_the_dpkg_records_of_the_base_are_there_for_the_scanners(stack: Stack) -> None:
    result = python_in_image(STATUS_D)
    assert result.returncode == 0, result.stderr[-500:]
    packages = set(result.stdout.split())
    for package in BASE_PACKAGES:
        assert package in packages, package


def test_every_shared_library_belongs_to_a_package_the_scanners_see(stack: Stack) -> None:
    result = python_in_image(UNACCOUNTED_LIBRARIES, timeout=180)
    assert result.returncode == 0, result.stderr[-500:]
    assert result.stdout.split() == [], f"libraries no scanner can attribute:\n{result.stdout}"


def test_the_stdlib_modules_without_their_system_library_are_gone(stack: Stack) -> None:
    result = python_in_image(NOT_MISSING, *REMOVED_STDLIB)
    assert result.returncode == 0, result.stderr[-500:]
    assert result.stdout.strip() == "", f"still in the image:\n{result.stdout}"


def test_every_compiled_module_of_python_imports(stack: Stack) -> None:
    result = python_in_image(IMPORT_LIB_DYNLOAD)
    assert result.returncode == 0, result.stderr[-500:]
    lines = result.stdout.splitlines()
    assert lines[0].startswith("modules ") and int(lines[0].split()[1]) > 40, lines[:1]
    assert lines[1:] == [], "compiled modules that do not import:\n" + "\n".join(lines[1:])
