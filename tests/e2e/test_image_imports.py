"""The image can import what the gateway needs and build the app (issue 54). Only `make e2e`.

A guard for changes of the base image (a distroless base, another libc, a missing system
library): the modules with compiled code must import inside the image, and `create_app()` must
build with throwaway keys passed by variable NAME (no value is ever in an argument) and the
panel on, so Jinja2 compiles its templates too. Nothing is served and there is no network.
Since PR B2: every module of every installed package imports, the NER pool runs with the fake
backend, and the NER switched on without its model is refused with a fixed message.
"""

import os
import secrets

import pytest

from tests.e2e.conftest import IMAGE, Stack, docker
from tests.e2e.image_probes import (
    BROWSER_CHECK_PACKAGES,
    BUILD_APP,
    EXPECTED_MISSING,
    HARDENED,
    IMPORT_ALL,
    IMPORT_EVERYTHING,
    INSTALLED_DISTRIBUTIONS,
    NER_FAKE_POOL,
    REQUIRED_MODULES,
    import_problems,
    python_in_image,
)

pytestmark = pytest.mark.e2e

KEY_VARS = ("ANTIFAZ_API_KEY", "ANTIFAZ_ADMIN_TOKEN")
# What `antifaz serve` says when the NER is on without its model (antifaz.detect.ner.setup).
NER_WITHOUT_MODEL = (
    "antifaz.config.UnsafeConfigError: ANTIFAZ_NER_MODEL_DIR is not set but "
    "ANTIFAZ_NER_ENABLED is true"
)
NER_EMPTY_MODEL_DIR = (
    "antifaz.config.UnsafeConfigError: ANTIFAZ_NER_MODEL_DIR does not match the model "
    "manifest: model file missing: gliner_config.json"
)


def test_the_image_imports_every_required_module(stack: Stack) -> None:
    result = python_in_image(IMPORT_ALL, *REQUIRED_MODULES)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"modules that do not import:\n{result.stdout}"


def test_the_image_imports_every_module_of_every_installed_package(stack: Stack) -> None:
    """Issue 54: a standard-library module was removed only because nothing installed needs it.

    At least 900 modules. Nothing inside `antifaz` and no top-level name may fail; any other
    module may fail only because a package of EXPECTED_MISSING (each with its reason) is not
    installed, or because it is Windows-only. Every listed package must really be missing.
    """
    result = python_in_image(IMPORT_EVERYTHING, timeout=300)
    assert result.returncode == 0, result.stderr[-2000:]
    problems = import_problems(result.stdout, EXPECTED_MISSING)
    assert problems == [], "\n".join(problems)


def test_the_image_ships_no_package_of_the_browser_checks(stack: Stack) -> None:
    """PR 11c-2a: Playwright, axe and what they bring live in the `panel` group, never here."""
    result = python_in_image(INSTALLED_DISTRIBUTIONS)
    assert result.returncode == 0, result.stderr[-2000:]
    installed = set(result.stdout.split())
    assert {"antifaz", "fastapi", "jinja2"} <= installed  # the listing is not empty
    assert not installed & BROWSER_CHECK_PACKAGES


def test_the_ner_process_pool_runs_in_the_image_with_the_fake_backend(stack: Stack) -> None:
    """The NER's own machinery (spawned worker, pipes, JSON) works without a shell. The real
    backend needs the `ner` extra, which the image does not install."""
    result = python_in_image(NER_FAKE_POOL, timeout=180)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == 'ok [[[10, 29, "person", 0.9]]]'


@pytest.mark.parametrize(
    ("model_dir", "message"),
    [(None, NER_WITHOUT_MODEL), ("/tmp", NER_EMPTY_MODEL_DIR)],  # noqa: S108
    ids=["no-model-dir", "empty-model-dir"],
)
def test_the_gateway_with_the_ner_on_refuses_to_start_with_a_fixed_message(
    stack: Stack, model_dir: str | None, message: str
) -> None:
    """With ANTIFAZ_NER_ENABLED=true and no model, `serve` stops with the fixed refusal of
    ADR-0016 (never an ImportError of a removed module, never a key)."""
    key = secrets.token_hex(32)
    environ = {k: v for k, v in os.environ.items() if not k.startswith("ANTIFAZ_")}
    environ["ANTIFAZ_API_KEY"] = key
    options = ["-e", "ANTIFAZ_API_KEY", "-e", "ANTIFAZ_NER_ENABLED=true", "--tmpfs", "/tmp"]  # noqa: S108
    if model_dir:
        options += ["-e", f"ANTIFAZ_NER_MODEL_DIR={model_dir}"]
    result = docker("run", "--rm", *HARDENED, *options, IMAGE, env=environ, timeout=120)
    output = result.stdout + result.stderr
    assert key not in output, "the throwaway key appeared in the output"
    assert result.returncode == 1
    assert output.strip().splitlines()[-1].startswith(message), output[-1000:]
    for word in ("ModuleNotFoundError", "ImportError", "ctypes", "lzma", "sqlite3"):
        assert word not in output, word


def test_the_image_builds_the_app_with_throwaway_settings(stack: Stack) -> None:
    keys = {name: secrets.token_hex(32) for name in KEY_VARS}
    environ = {k: v for k, v in os.environ.items() if not k.startswith("ANTIFAZ_")} | keys
    options = tuple(arg for name in KEY_VARS for arg in ("-e", name))
    result = python_in_image(BUILD_APP, options=options, env=environ)
    output = result.stdout + result.stderr
    leaked = any(key in output for key in keys.values())
    assert not leaked, "a throwaway key appeared in the output"
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "FastAPI True"
