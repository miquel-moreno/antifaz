"""«Prueba un texto» when the NER fails (issue 53, PR 11d-b): an error, never a nameless result.

With the NER on and no `"ner": false` from the user, a Try whose NER fails answers with one of
the fixed errors: the blocked answer (`detector_failed`: nothing sent, no entity, no segment) or
503 `ner_busy`. It never answers `outcome: "masked"` with the names unchecked. Only an explicit
`"ner": false` gives a result without the detector. Each failure mode with the real process pool
and the fake backend's misbehaving words (as in tests/unit/detect/ner/test_try_path.py):

- the worker dies in the middle of the Try (FAKE_CRASH);
- the worker passes the Try's deadline and is killed (FAKE_SLEEP with a 1 s Try deadline);
- the circuit is open (a proxy request broke the only worker; the threshold is 1);
- no worker is free for a Try (a fresh worker that has served no proxy request yet is always
  the proxy's: NerPool._take_for_try).
"""

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from antifaz.detect.ner.engine import NerDetector
from antifaz.detect.ner.pool import NerPool
from antifaz.errors import DetectorFailed
from tests.conftest import SENTINEL_DNI
from tests.integration.panel_client import HTTPS_URL, action_headers, logged_in, panel_client
from tests.nerfakes import CARMEN, FAKE_FACTORY, NAMES

TRY = "/panel/api/try"
LABELS = ["person"]


def _pool(**options: Any) -> NerPool:
    return NerPool(
        FAKE_FACTORY,
        {"names": dict(NAMES), "triggers": True, "sleep_seconds": 30},
        workers=1,
        timeout=20.0,
        failure_threshold=1,
        backoff_seconds=60.0,
        max_backoff_seconds=60.0,
        **options,
    )


@pytest.fixture
def pool() -> Iterator[NerPool]:
    pool = _pool()
    yield pool
    pool.close()


def _try(client: TestClient, text: str, **extra: object) -> httpx.Response:
    session_id, token = logged_in(client)
    content = json.dumps({"text": text, "lang": "es", **extra}).encode()
    return client.post(TRY, content=content, headers=action_headers(client, session_id, token))


def _serve_one_proxy_request(pool: NerPool) -> None:
    """A fresh worker is the proxy's until it has answered once (its first inference)."""
    assert pool.predict(["hola"], LABELS, 0.5) == [[]]


def _never_a_result_without_names(response: httpx.Response) -> None:
    assert response.status_code in (200, 503), response.status_code
    assert SENTINEL_DNI not in response.text and CARMEN not in response.text
    if response.status_code == 503:
        assert response.json()["error"]["code"] == "ner_busy"
        return
    data = response.json()
    assert (data["outcome"], data["blocked_reason"]) == ("blocked", "detector_failed")
    assert data["names_checked"] is False
    assert data["entities"] == [] and data["sent"] == [] and data["reply"] == []


def test_a_worker_that_dies_in_the_try_is_an_error(pool: NerPool) -> None:
    with panel_client(HTTPS_URL, ner=NerDetector(pool)) as client:
        _serve_one_proxy_request(pool)

        response = _try(client, f"FAKE_CRASH Soy {CARMEN}, DNI {SENTINEL_DNI}")

    _never_a_result_without_names(response)
    assert response.status_code == 200  # blocked: the worker was asked and died


def test_a_try_past_its_deadline_is_an_error(
    pool: NerPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from antifaz.api import app as app_module

    monkeypatch.setattr(app_module, "TRY_NER_SECONDS", 1.0)
    with panel_client(HTTPS_URL, ner=NerDetector(pool)) as client:
        _serve_one_proxy_request(pool)

        response = _try(client, f"FAKE_SLEEP Soy {CARMEN}, DNI {SENTINEL_DNI}")

    _never_a_result_without_names(response)
    assert response.status_code == 200  # blocked: the watchdog killed the worker


def test_an_open_circuit_is_an_error(pool: NerPool) -> None:
    with panel_client(HTTPS_URL, ner=NerDetector(pool)) as client:
        _serve_one_proxy_request(pool)
        with pytest.raises(DetectorFailed):
            pool.predict(["FAKE_CRASH"], LABELS, 0.5)  # a proxy failure: the circuit opens
        assert pool.status() == "circuit_open"

        response = _try(client, f"Soy {CARMEN}, DNI {SENTINEL_DNI}")

    _never_a_result_without_names(response)
    assert response.json()["ner"] == "circuit_open"


def test_no_free_worker_is_503_ner_busy(pool: NerPool) -> None:
    with panel_client(HTTPS_URL, ner=NerDetector(pool)) as client:
        response = _try(client, f"Soy {CARMEN}, DNI {SENTINEL_DNI}")  # served nothing yet

    _never_a_result_without_names(response)
    assert response.status_code == 503


@pytest.mark.parametrize(
    "text",
    [f"FAKE_CRASH Soy {CARMEN}, DNI {SENTINEL_DNI}", f"Soy {CARMEN}, DNI {SENTINEL_DNI}"],
)
def test_only_an_explicit_ner_false_gives_a_result_without_the_detector(
    pool: NerPool, text: str
) -> None:
    with panel_client(HTTPS_URL, ner=NerDetector(pool)) as client:
        response = _try(client, text, ner=False)  # the pool is never asked: no worker breaks

    data = response.json()
    assert response.status_code == 200
    assert (data["outcome"], data["names_checked"]) == ("masked", False)
    assert [s["type"] for s in data["sent"] if "marker" in s] == ["ES_DNI"]
