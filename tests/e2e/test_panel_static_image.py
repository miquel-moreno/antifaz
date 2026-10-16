"""The panel's static files in the real image (issue 53, ADR-0018, PR 11c-1). Only `make e2e`.

The image built by the `stack` fixture runs once more with ANTIFAZ_ADMIN_TOKEN (throwaway keys
made here, passed by variable NAME so no value is ever in an argument), the README's hardening
and no network (the requests are made from inside the container). The login page names its
static files; one is fetched and must be the package's bytes with the immutable
Cache-Control. The OFL texts must be in the image next to the fonts.
"""

import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from antifaz.web.assets import load_assets
from tests.e2e.conftest import IMAGE, REPO, Stack, docker
from tests.e2e.image_probes import FETCH_LOCAL

pytestmark = pytest.mark.e2e

STATIC = REPO / "src" / "antifaz" / "web" / "static"
KEY_VAR, ADMIN_VAR = "ANTIFAZ_API_KEY", "ANTIFAZ_ADMIN_TOKEN"


@dataclass
class Fetched:
    status: int
    headers: dict[str, str]
    body: bytes


def _fetch(name: str, path: str) -> Fetched | None:
    """GET http://127.0.0.1:8000<path> from inside the container (it has no network)."""
    url = f"http://127.0.0.1:8000{path}"
    result = docker("exec", name, "python", "-c", FETCH_LOCAL, url, timeout=30)
    if result.returncode != 0:
        return None
    data = json.loads(result.stdout)
    return Fetched(data["status"], data["headers"], base64.b64decode(data["body"]))


def _wait_login(name: str, timeout: float = 90) -> Fetched | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = _fetch(name, "/panel/login")
        if response is not None and response.status == 200:
            return response
        time.sleep(1)
    return None


def test_the_image_serves_a_static_file_with_its_bytes_and_cache(stack: Stack) -> None:
    name = f"antifaz-e2e-static-{secrets.token_hex(4)}"
    environ = os.environ | {KEY_VAR: secrets.token_hex(32), ADMIN_VAR: secrets.token_hex(32)}
    # The README's hardening with --network none: the requests are made from inside.
    command = [
        "docker", "run", "-d", "--name", name, "-e", KEY_VAR, "-e", ADMIN_VAR,
        "-e", "ANTIFAZ_ALLOWED_HOSTS=localhost,127.0.0.1", "--network", "none",
        "--read-only", "--tmpfs", "/tmp:size=16m,mode=1777,noexec,nosuid,nodev",  # noqa: S108
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        IMAGE,
    ]  # fmt: skip
    try:
        started = subprocess.run(
            command, capture_output=True, text=True, env=environ, timeout=120, check=False
        )
        assert started.returncode == 0, started.stderr
        login = _wait_login(name)
        assert login is not None, docker("logs", name).stderr[-2000:]
        assert login.headers["cache-control"] == "no-store"
        page = login.body.decode("utf-8")
        (css,) = re.findall(r'href="(/panel/static/panel\.[0-9a-f]{12}\.css)"', page)

        response = _fetch(name, css)

        assert response is not None
        assert response.status == 200
        assert response.headers["content-type"] == "text/css; charset=utf-8"
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert css == load_assets().urls["panel.css"]  # the same files as this checkout
        assert response.body == load_assets().files[css.removeprefix("/panel/static/")].body
        licence = _fetch(name, "/panel/static/Inter-OFL.txt")
        assert licence is not None
        assert licence.status == 401
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
