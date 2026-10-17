import ipaddress
import os
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from antifaz.api.app import create_app
from antifaz.config import Settings

# An invented but valid DNI (checksum letter correct), used as a "sentinel": it must never
# appear in logs, error bodies or metrics.
SENTINEL_DNI = "12345678Z"

# The panel in real browsers (tests/browser, PR 11c-2a): only with --panel (`make panel-check`).
BROWSER_TESTS = Path(__file__).parent / "browser"
BROWSERS = ("chromium", "firefox", "webkit")
ALL_BROWSERS = ",".join(BROWSERS)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--e2e",
        action="store_true",
        default=False,
        help="run the end-to-end tests (Docker Compose, tests/e2e): `make e2e`",
    )
    parser.addoption(
        "--panel",
        action="store_true",
        default=False,
        help="run the panel in real browsers (Playwright, tests/browser): `make panel-check`",
    )
    parser.addoption(
        "--panel-browser",
        default=ALL_BROWSERS,
        help=f"comma list of the browsers for --panel (default {ALL_BROWSERS})",
    )


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """tests/browser is not even collected without --panel: `make test` never imports
    Playwright (only the `panel` dependency group installs it)."""
    if config.getoption("--panel"):
        return None
    if collection_path == BROWSER_TESTS or BROWSER_TESTS in collection_path.parents:
        return True
    return None


def panel_browsers(config: pytest.Config) -> list[str]:
    """The browsers of --panel-browser, in order: known, not empty, not repeated."""
    names = [name.strip() for name in str(config.getoption("--panel-browser")).split(",")]
    if any(name not in BROWSERS for name in names) or len(set(names)) != len(names):
        raise pytest.UsageError(f"--panel-browser takes a comma list of {ALL_BROWSERS}")
    return names


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Tests marked `ner_model` need the real model (issue 6b): skipped unless it is there.
    Tests marked `e2e` need Docker and only run with --e2e (`make e2e`, issue 7a)."""
    skip_e2e = pytest.mark.skip(reason="end-to-end test with Docker: run `make e2e`")
    if not config.getoption("--e2e"):
        for item in items:
            if "e2e" in item.keywords:
                item.add_marker(skip_e2e)
    if os.environ.get("ANTIFAZ_NER_MODEL_DIR"):
        return
    skip = pytest.mark.skip(reason="needs the real NER model: set ANTIFAZ_NER_MODEL_DIR")
    for item in items:
        if "ner_model" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(
        antifaz_api_key=SecretStr("test-health-key-not-real-0123456789abcdef"),
        allowed_hosts=["testserver"],
        _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings)) as c:
        yield c


def _loopback(host: Any) -> bool:
    if host in (None, "localhost"):
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


def forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only loopback connections (the event loop uses one on Windows): name lookups and
    socket connects to anything else fail, so a test can never reach a real provider."""
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if not _loopback(host):
            raise RuntimeError("tests must not open network connections")
        return real_getaddrinfo(host, *args, **kwargs)

    def check(sock: socket.socket, address: Any) -> None:
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address[0]):
            raise RuntimeError("tests must not open network connections")

    def connect(sock: socket.socket, address: Any) -> None:
        check(sock, address)
        real_connect(sock, address)

    def connect_ex(sock: socket.socket, address: Any) -> int:
        check(sock, address)
        return real_connect_ex(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
