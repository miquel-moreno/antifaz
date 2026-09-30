import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from antifaz.api.errors import AppError, register_error_handlers
from antifaz.logging import JsonFormatter, configure_logging, request_id_var
from tests.conftest import SENTINEL_DNI


class TeapotError(AppError):
    status_code = 418
    code = "teapot"


class Body(BaseModel):
    count: int
    scores: dict[str, int] = {}
    tags: list[int] = []


def app_with_routes() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/teapot")
    async def teapot() -> None:
        raise TeapotError("short and stout")

    @app.post("/echo")
    async def echo(body: Body) -> Body:
        return body

    return app


def test_app_error_is_returned_as_json() -> None:
    response = TestClient(app_with_routes()).get("/teapot")

    assert response.status_code == 418
    assert response.json() == {"error": {"code": "teapot", "message": "short and stout"}}


def test_validation_errors_never_echo_the_received_body() -> None:
    response = TestClient(app_with_routes()).post(
        "/echo", json={"count": f"DNI {SENTINEL_DNI}", "extra": SENTINEL_DNI}
    )

    assert response.status_code == 422
    assert SENTINEL_DNI not in response.text
    assert response.json() == {
        "error": {"code": "invalid_request", "message": "invalid request: check body.*"}
    }


def test_validation_errors_never_echo_keys_the_client_chose() -> None:
    response = TestClient(app_with_routes()).post(
        "/echo", json={"count": 1, "scores": {SENTINEL_DNI: "x"}, "tags": [1, "x"]}
    )

    assert response.status_code == 422
    assert SENTINEL_DNI not in response.text
    assert response.json()["error"]["message"] == "invalid request: check body.*.*, body.*.1"


def test_json_formatter_includes_request_id() -> None:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    token = request_id_var.set("req-1")
    try:
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)

    assert line["msg"] == "hello world"
    assert line["request_id"] == "req-1"
    assert line["level"] == "INFO"


def test_validation_error_message_has_a_maximum_size() -> None:
    body = {"count": 1, "tags": ["x"] * 500}

    message = TestClient(app_with_routes()).post("/echo", json=body).json()["error"]["message"]

    assert message.count("body.") == 10 and message.endswith(", ...")


def test_configure_logging_replaces_only_its_own_handler() -> None:
    root = logging.getLogger()
    foreign = logging.NullHandler()  # e.g. pytest's caplog handler
    root.addHandler(foreign)
    try:
        configure_logging("INFO")
        configure_logging("INFO")

        ours = [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]
        assert len(ours) == 1
        assert foreign in root.handlers
    finally:
        root.removeHandler(foreign)
