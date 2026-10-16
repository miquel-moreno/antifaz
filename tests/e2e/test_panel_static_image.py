"""The panel's static files in the real image (issue 53, ADR-0018, PR 11c-1). Only `make e2e`.

The image built by the `stack` fixture runs once more with ANTIFAZ_ADMIN_TOKEN (throwaway keys
made here, passed by variable NAME so no value is ever in an argument) and the README's
hardening. The login page names its static files; one is fetched and must be the package's
bytes with the immutable Cache-Control. The OFL texts must be in the image next to the fonts.
"""

import hashlib
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from antifaz.web.assets import load_assets
from tests.e2e.conftest import IMAGE, REPO, Stack, _free_port, docker

pytestmark = pytest.mark.e2e

STATIC = REPO / "src" / "antifaz" / "web" / "static"
KEY_VAR, ADMIN_VAR = "ANTIFAZ_API_KEY", "ANTIFAZ_ADMIN_TOKEN"


def _wait_login(url: str, timeout: float = 90) -> httpx.Response | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{url}/panel/login", timeout=5)
            if response.status_code == 200:
                return response
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return None


def test_the_image_serves_a_static_file_with_its_bytes_and_cache(stack: Stack) -> None:
    port = _free_port()
    name = f"antifaz-e2e-static-{secrets.token_hex(4)}"
    environ = os.environ | {KEY_VAR: secrets.token_hex(32), ADMIN_VAR: secrets.token_hex(32)}
    command = [
        "docker", "run", "-d", "--name", name, "-e", KEY_VAR, "-e", ADMIN_VAR,
        "-e", "ANTIFAZ_ALLOWED_HOSTS=localhost,127.0.0.1",
        "-p", f"127.0.0.1:{port}:8000",
        "--read-only", "--tmpfs", "/tmp:size=16m,mode=1777,noexec,nosuid,nodev",  # noqa: S108
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        IMAGE,
    ]  # fmt: skip
    try:
        started = subprocess.run(
            command, capture_output=True, text=True, env=environ, timeout=120, check=False
        )
        assert started.returncode == 0, started.stderr
        login = _wait_login(f"http://127.0.0.1:{port}")
        assert login is not None, docker("logs", name).stderr[-2000:]
        assert login.headers["cache-control"] == "no-store"
        (css,) = re.findall(r'href="(/panel/static/panel\.[0-9a-f]{12}\.css)"', login.text)

        response = httpx.get(f"http://127.0.0.1:{port}{css}", timeout=10)

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/css; charset=utf-8"
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert css == load_assets().urls["panel.css"]  # the same files as this checkout
        assert response.content == load_assets().files[css.removeprefix("/panel/static/")].body
        licence = httpx.get(f"http://127.0.0.1:{port}/panel/static/Inter-OFL.txt", timeout=10)
        assert licence.status_code == 401
    finally:
        docker("rm", "-f", name)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_image_holds_the_ofl_texts_next_to_the_fonts(stack: Stack) -> None:
    probe = (
        "import hashlib, pathlib, antifaz.web\n"
        "static = pathlib.Path(antifaz.web.__file__).parent / 'static'\n"
        "for name in sorted(p.name for p in static.iterdir()):\n"
        "    print(name, hashlib.sha256((static / name).read_bytes()).hexdigest())\n"
    )
    result = docker(
        "run", "--rm", "--network", "none", "--entrypoint", "python", IMAGE, "-c", probe
    )
    assert result.returncode == 0, result.stderr
    shipped = dict(line.split() for line in result.stdout.splitlines())

    for name in (
        "Inter-OFL.txt",
        "GeistMono-OFL.txt",
        "InterVariable.woff2",
        "GeistMono-Variable.woff2",
    ):
        assert shipped.get(name) == _sha256(STATIC / name), name
