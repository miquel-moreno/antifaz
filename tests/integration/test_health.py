from fastapi.testclient import TestClient

from antifaz import __version__


def test_healthz_returns_ok_and_version(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_response_includes_generated_request_id(client: TestClient) -> None:
    response = client.get("/healthz")

    assert len(response.headers["X-Request-ID"]) == 32


def test_incoming_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"


def test_a_request_id_that_could_carry_data_is_replaced(client: TestClient) -> None:
    for unsafe in ("DNI 12345678Z", "a" * 65, "x@example.com"):
        response = client.get("/healthz", headers={"X-Request-ID": unsafe})

        assert response.headers["X-Request-ID"] != unsafe
        assert len(response.headers["X-Request-ID"]) == 32
