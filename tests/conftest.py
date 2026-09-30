from collections.abc import Iterator

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
