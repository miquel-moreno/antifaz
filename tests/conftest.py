from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from antifaz.api.app import create_app

# An invented but valid DNI (checksum letter correct), used as a "sentinel": it must never
# appear in logs, error bodies or metrics.
SENTINEL_DNI = "12345678Z"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c
