"""The request-id middleware as plain ASGI (11e-1 review): it pins what BaseHTTPMiddleware did.

Every answer, whatever produced it (a proxied SSE stream, an AppError, the panel's door, the
trusted-host check, a panel page, the live stream), carries exactly one X-Request-ID of 32 hex
characters and exactly one `X-Content-Type-Options: nosniff`. The access line is written once,
with the request id in context, and never holds the query string or a header value.
"""

import logging
import re
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from antifaz.logging import request_id_var
from tests.conftest import SENTINEL_DNI
from tests.integration.fakes import GATEWAY_KEY
from tests.integration.live_client import Stream, build_app, log_in, running
from tests.integration.panel_client import HTTPS_URL, panel_client

AUTH = {"Authorization": f"Bearer {GATEWAY_KEY}"}
CHAT = {"model": "m", "messages": [{"role": "user", "content": "hola"}]}
REQUEST_ID = re.compile(r"[0-9a-f]{32}")


@pytest.fixture
def client() -> Iterator[TestClient]:
    with panel_client(HTTPS_URL) as client:
        yield client


def _assert_headers(headers: list[tuple[str, str]]) -> str:
    ids = [value for name, value in headers if name.lower() == "x-request-id"]
    nosniff = [value for name, value in headers if name.lower() == "x-content-type-options"]
    assert len(ids) == 1 and REQUEST_ID.fullmatch(ids[0]), ids
    assert nosniff == ["nosniff"]
    return ids[0]


Call = Callable[[TestClient], httpx.Response]
CASES: dict[str, tuple[Call, int]] = {
    "proxied-sse": (
        lambda c: c.post("/v1/chat/completions", json={**CHAT, "stream": True}, headers=AUTH),
        200,
    ),
    "app-error-4xx": (
        lambda c: c.post(
            "/v1/chat/completions",
            content=b"{not json",
            headers={**AUTH, "Content-Type": "application/json"},
        ),
        400,
    ),
    "door-401": (lambda c: c.get("/panel/api/status"), 401),
    "door-403": (
        lambda c: c.get("/panel/api/status", headers={"Sec-Fetch-Site": "cross-site"}),
        403,
    ),
    "trusted-host-400": (lambda c: c.get("/healthz", headers={"Host": "evil.example"}), 400),
    "panel-html": (lambda c: c.get("/panel/login"), 200),
}


@pytest.mark.parametrize("case", list(CASES))
def test_every_answer_has_one_request_id_and_nosniff(client: TestClient, case: str) -> None:
    call, status = CASES[case]
    response = call(client)

    assert response.status_code == status, response.text
    if case == "proxied-sse":
        assert response.headers["content-type"].startswith("text/event-stream")
    _assert_headers(list(response.headers.multi_items()))


async def test_the_live_stream_has_one_request_id_and_nosniff() -> None:
    app = build_app()
    async with running(app) as plain:
        session_id = await log_in(plain)
        async with Stream(app, session_id) as stream:
            assert stream.status == 200
            await stream.event()
    headers = [(name.decode(), value.decode()) for name, value in stream.headers]
    _assert_headers(headers)


class _Lines(logging.Handler):
    """The access lines, each with the request id in context when it was written."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[tuple[str, str | None]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append((record.getMessage(), request_id_var.get()))


def test_the_access_line_has_the_request_id_and_never_the_query_or_a_header(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    lines = _Lines()
    logger = logging.getLogger("http")
    logger.addHandler(lines)
    try:
        with caplog.at_level(logging.DEBUG):
            response = client.post(
                f"/v1/chat/completions?dni={SENTINEL_DNI}",
                json=CHAT,
                headers={**AUTH, "X-Probe": f"probe-{SENTINEL_DNI}", "User-Agent": "probe-agent"},
            )
    finally:
        logger.removeHandler(lines)

    assert response.status_code == 200
    ((message, logged_id),) = lines.lines
    assert re.fullmatch(r"POST /v1/chat/completions -> 200 \(\d+\.\d ms\)", message), message
    assert logged_id == response.headers["x-request-id"]
    for secret in (SENTINEL_DNI, "dni=", GATEWAY_KEY, "probe", "?"):
        assert secret not in message
        assert secret not in caplog.text
