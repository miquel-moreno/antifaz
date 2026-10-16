"""The image can import what the gateway needs and build the app (issue 54). Only `make e2e`.

A guard for changes of the base image (a distroless base, another libc, a missing system
library): the modules with compiled code must import inside the image, and `create_app()` must
build with throwaway keys passed by variable NAME (no value is ever in an argument) and the
panel on, so Jinja2 compiles its templates too. Nothing is served and there is no network.
"""

import os
import secrets

import pytest

from tests.e2e.conftest import Stack
from tests.e2e.image_probes import BUILD_APP, IMPORT_ALL, REQUIRED_MODULES, python_in_image

pytestmark = pytest.mark.e2e

KEY_VARS = ("ANTIFAZ_API_KEY", "ANTIFAZ_ADMIN_TOKEN")


def test_the_image_imports_every_required_module(stack: Stack) -> None:
    result = python_in_image(IMPORT_ALL, *REQUIRED_MODULES)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"modules that do not import:\n{result.stdout}"


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
