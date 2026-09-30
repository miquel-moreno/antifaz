import ipaddress
import socket
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from antifaz.api.app import create_app
from antifaz.config import Settings

# An invented but valid DNI (checksum letter correct), used as a "sentinel": it must never
# appear in logs, error bodies or metrics.
SENTINEL_DNI = "12345678Z"


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
