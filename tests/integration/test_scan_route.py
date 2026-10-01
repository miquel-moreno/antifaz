"""POST /antifaz/scan: types and positions of the personal data in a text, never the values."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from antifaz.detect.scan import Scanner
from tests.integration.fakes import GATEWAY_KEY, FakeUpstream, models_list
from tests.nerfakes import CARMEN, fake_detector
from tests.redteam.conftest import DNI, IBAN, gateway_client

AUTH = {"Authorization": f"Bearer {GATEWAY_KEY}"}


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream(models_list)


@pytest.fixture
def client(upstream: FakeUpstream) -> Iterator[TestClient]:
    yield from gateway_client(upstream)


def test_it_returns_types_and_positions(client: TestClient, upstream: FakeUpstream) -> None:
    text = f"Mi DNI es {DNI} y mi IBAN {IBAN}."

    response = client.post("/antifaz/scan", json={"text": text}, headers=AUTH)

    assert response.status_code == 200
    entities = response.json()["entities"]
    assert entities == [
        {"type": "ES_DNI", "start": text.index(DNI), "end": text.index(DNI) + len(DNI)},
        {"type": "IBAN", "start": text.index(IBAN), "end": text.index(IBAN) + len(IBAN)},
    ]
    assert DNI not in response.text
    assert IBAN not in response.text
    assert upstream.requests == []  # nothing goes to a provider


def test_text_without_personal_data_gives_an_empty_list(client: TestClient) -> None:
    for text in ("", "hola, ¿qué tal?"):
        response = client.post("/antifaz/scan", json={"text": text}, headers=AUTH)

        assert response.status_code == 200
        assert response.json() == {"entities": []}


def test_positions_count_unicode_characters(client: TestClient) -> None:
    text = f"🙂 ñ {DNI}"  # an emoji is one character (two UTF-16 units, four UTF-8 bytes)

    entities = client.post("/antifaz/scan", json={"text": text}, headers=AUTH).json()["entities"]

    assert entities == [{"type": "ES_DNI", "start": 4, "end": 13}]


def test_it_uses_the_gateway_detector_with_the_ner(upstream: FakeUpstream) -> None:
    ner, predictor = fake_detector()
    text = f"{CARMEN} escribió. Firmado: {CARMEN}"
    for client in gateway_client(upstream, detector=Scanner(ner)):
        response = client.post("/antifaz/scan", json={"text": text}, headers=AUTH)

        assert response.status_code == 200
        starts = [e["start"] for e in response.json()["entities"] if e["type"] == "PERSON"]
        assert starts == [0, text.rindex(CARMEN)]
        assert CARMEN not in response.text
    assert predictor.calls >= 1


def test_the_response_has_only_the_three_fields(client: TestClient) -> None:
    response = client.post("/antifaz/scan", json={"text": f"DNI {DNI}"}, headers=AUTH)

    assert response.headers["content-type"] == "application/json"
    assert list(response.json()) == ["entities"]
    assert [list(entity) for entity in response.json()["entities"]] == [["type", "start", "end"]]
