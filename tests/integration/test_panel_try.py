"""POST /panel/api/try through the real door (issue 53, PR 11d-a, ADR-0018).

- The door: no session or only the API key, 401; a missing or wrong CSRF token, a wrong Origin,
  Sec-Fetch-Site cross-site or same-site, or a text/plain body, 403. GET and HEAD, 405.
- The body: exactly {"text", "lang"} (else 400 invalid_try_body), at most 128 KiB raw with or
  without Content-Length and a text of at most 20 480 UTF-8 bytes (else 413 text_too_large),
  read within the deadline (else 408).
- The limit: 30 tries in 60 s per session (the 31st is 429 too_many_tries with Retry-After),
  counted before the body is read; another session is not affected; a session that closes
  leaves nothing in the table.
- The pipeline: the app's own detector and policy (the NER too, with a fake backend), the guard,
  a simulated reply and restore. No provider is ever called: the provider transport raises.
- The answer: the closed schema, the door's headers with no-store, no Vary or Content-Language.
"""

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from antifaz.web import routes, try_text
from antifaz.web.headers import NO_STORE, SECURITY_HEADERS
from antifaz.web.try_response import TryResponse
from tests.conftest import SENTINEL_DNI
from tests.integration.fakes import GATEWAY_KEY, FakeUpstream
from tests.integration.panel_client import (
    HTTPS_URL,
    LOCAL_URL,
    action_headers,
    clients,
    cookie_of,
    log_in,
    logged_in,
    panel_client,
    post_logout,
)
from tests.nerfakes import CARMEN, NAMES, InProcess, fake_detector

TRY = "/panel/api/try"
EMAIL = "ana@example.com"


@pytest.fixture(params=[HTTPS_URL, LOCAL_URL])
def client(request: pytest.FixtureRequest) -> Iterator[TestClient]:
    yield from clients(request.param)


def body(text: str = f"Mi DNI es {SENTINEL_DNI}", lang: str = "es") -> bytes:
    return json.dumps({"text": text, "lang": lang}).encode()


def post_try(
    client: TestClient,
    session_id: str | None,
    token: str | None,
    content: Any = None,
    **extra: str,
) -> httpx.Response:
    headers = action_headers(client, session_id, token, **extra)
    return client.post(TRY, content=body() if content is None else content, headers=headers)


# --- the door ---------------------------------------------------------------------------------


def test_a_live_session_with_its_token_gets_the_answer(client: TestClient) -> None:
    session_id, token = logged_in(client)

    response = post_try(client, session_id, token)

    assert response.status_code == 200, response.text
    data = response.json()
    TryResponse.model_validate_json(response.content)
    assert data["outcome"] == "masked"
    assert [s for s in data["sent"] if "marker" in s] == [
        {"marker": "[[ES_DNI_1]]", "type": "ES_DNI"}
    ]
    assert [s["value"] for s in data["reply"] if "marker" in s] == [SENTINEL_DNI]
    assert data["ner"] == "off"
    assert data["policy_version"] == "builtin-1"


def test_the_answer_has_the_door_headers_and_no_store(client: TestClient) -> None:
    session_id, token = logged_in(client)

    response = post_try(client, session_id, token)

    assert response.headers["cache-control"] == NO_STORE
    for name, value in SECURITY_HEADERS:
        if name != "cache-control":
            assert response.headers.get_list(name) == [value], name
    assert response.headers["content-type"] == "application/json"
    assert "vary" not in response.headers
    assert "content-language" not in response.headers
    assert "set-cookie" not in response.headers


def test_without_a_session_it_is_401(client: TestClient) -> None:
    response = post_try(client, None, "x")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "panel_unauthorized"


def test_the_api_key_does_not_open_it(client: TestClient) -> None:
    for auth in ({"Authorization": f"Bearer {GATEWAY_KEY}"}, {"x-api-key": GATEWAY_KEY}):
        response = post_try(client, None, None, **auth)

        assert response.status_code == 401


def test_a_closed_session_is_401(client: TestClient) -> None:
    session_id, token = logged_in(client)
    post_logout(client, session_id, token)

    assert post_try(client, session_id, token).status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        {"token": None},
        {"token": "wrong-token"},
        {"Origin": "https://evil.example"},
        {"Origin": ""},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Sec-Fetch-Site": ""},
        {"Content-Type": "text/plain"},
        {"Content-Type": "application/x-www-form-urlencoded"},
        {"Content-Type": "multipart/form-data; boundary=x"},
    ],
)
def test_every_csrf_layer_is_checked(client: TestClient, change: dict[str, str | None]) -> None:
    session_id, token = logged_in(client)
    extra = {k: v for k, v in change.items() if k != "token"}
    sent_token = change.get("token", token)

    response = post_try(client, session_id, sent_token, **extra)  # type: ignore[arg-type]

    assert response.status_code == 403, change
    assert response.json()["error"]["code"] == "csrf_rejected"


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_get_and_head_are_405(client: TestClient, method: str) -> None:
    session_id = log_in(client)
    headers = {"Cookie": f"{cookie_of(client)}={session_id}", "Sec-Fetch-Site": "same-origin"}

    response = client.request(method, TRY, headers=headers)

    assert response.status_code == 405
    assert response.headers["cache-control"] == NO_STORE


# --- the body ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"{}",
        b'{"text": "x"}',
        b'{"text": "x", "lang": "es", "more": 1}',
        b'{"text": "x", "text": "y", "lang": "es"}',
        b'{"text": 5, "lang": "es"}',
        b'{"text": "x", "lang": "fr"}',
        b'{"text": NaN, "lang": "es"}',
        b'{"text": "\\ud800", "lang": "es"}',
        b"not json",
    ],
)
def test_a_bad_body_is_400_without_quoting_it(client: TestClient, content: bytes) -> None:
    session_id, token = logged_in(client)

    response = post_try(client, session_id, token, content)

    assert response.status_code == 400
    assert response.json() == {
        "error": {"code": "invalid_try_body", "message": try_text.TryBodyError.message}
    }


def test_413_when_the_declared_length_is_too_large(client: TestClient) -> None:
    session_id, token = logged_in(client)
    huge = b" " * (try_text.MAX_BODY_BYTES + 1)

    response = post_try(client, session_id, token, huge)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "text_too_large"


def test_a_body_that_never_ends_is_cut_by_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.types import Message

    from tests.redteam.test_panel_door import _queue_receive, _run_raw

    monkeypatch.setattr(routes, "TRY_BODY_SECONDS", 0.05)
    with panel_client(LOCAL_URL) as client:
        session_id, token = logged_in(client)
        headers = [
            (name.lower().encode(), value.encode())
            for name, value in action_headers(client, session_id, token).items()
        ]
        for content_length in (None, b"20"):
            messages: list[Message] = [
                {"type": "http.request", "body": b'{"text": "', "more_body": True}
            ]
            sent_headers = list(headers)
            if content_length is not None:
                sent_headers.append((b"content-length", content_length))

            sent = _run_raw(
                client.app,  # type: ignore[arg-type]
                "POST",
                TRY,
                sent_headers,
                _queue_receive(messages, then_wait=True),
            )

            start = next(m for m in sent if m["type"] == "http.response.start")
            assert start["status"] == 408
            assert (b"cache-control", b"no-store") in start["headers"]
            raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
            assert json.loads(raw)["error"]["code"] == "body_timeout"


def test_413_when_a_streamed_body_without_length_grows_too_large(client: TestClient) -> None:
    session_id, token = logged_in(client)

    def chunks() -> Iterator[bytes]:
        yield b'{"text": "'
        for _ in range(200):
            yield b"a" * 1024

    response = post_try(client, session_id, token, chunks())

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "text_too_large"


def test_413_when_the_text_is_over_its_limit_in_utf8_bytes(client: TestClient) -> None:
    session_id, token = logged_in(client)
    fits = body("é" * (try_text.MAX_TEXT_BYTES // 2))
    too_long = body("é" * (try_text.MAX_TEXT_BYTES // 2 + 1))

    assert post_try(client, session_id, token, fits).status_code == 200
    response = post_try(client, session_id, token, too_long)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "text_too_large"


def test_the_body_deadline_is_five_seconds() -> None:
    assert routes.TRY_BODY_SECONDS == try_text.BODY_SECONDS == 5


# --- the rate limit ---------------------------------------------------------------------------


def test_the_31st_try_in_a_minute_is_429_with_retry_after(client: TestClient) -> None:
    session_id, token = logged_in(client)
    for _ in range(30):
        assert post_try(client, session_id, token, body("hola")).status_code == 200

    response = post_try(client, session_id, token, body("hola"))

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "too_many_tries"
    assert 1 <= int(response.headers["retry-after"]) <= 60
    assert response.headers["cache-control"] == NO_STORE


def test_bad_bodies_count_too_and_a_refused_try_reads_nothing(client: TestClient) -> None:
    session_id, token = logged_in(client)
    for _ in range(30):
        assert post_try(client, session_id, token, b"{}").status_code == 400

    huge = b" " * (try_text.MAX_BODY_BYTES + 1)
    assert post_try(client, session_id, token, huge).status_code == 429  # not 413: not read


def test_another_session_is_not_affected(client: TestClient) -> None:
    first, first_token = logged_in(client)
    second, second_token = logged_in(client)
    for _ in range(30):
        post_try(client, first, first_token, body("x"))

    assert post_try(client, first, first_token, body("x")).status_code == 429
    assert post_try(client, second, second_token, body("x")).status_code == 200


def test_a_session_that_closes_leaves_nothing_in_the_table(client: TestClient) -> None:
    session_id, token = logged_in(client)
    limiter = _panel_state(client).try_limiter
    post_try(client, session_id, token, body("x"))
    assert len(limiter) == 1

    post_logout(client, session_id, token)

    assert len(limiter) == 0
    again, again_token = logged_in(client)
    assert post_try(client, again, again_token, body("x")).status_code == 200


def _panel_state(client: TestClient) -> Any:
    return client.app.state.panel_state  # type: ignore[attr-defined]


# --- the pipeline -----------------------------------------------------------------------------


def test_no_provider_is_ever_called() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a provider was called")

    upstream = FakeUpstream(refuse)
    with panel_client(HTTPS_URL, upstream=upstream) as client:
        session_id, token = logged_in(client)
        for text in (f"{SENTINEL_DNI} {EMAIL}", "nada", f"[[ES_DNI_1]] {SENTINEL_DNI}"):
            response = post_try(client, session_id, token, body(text))
            assert response.status_code == 200

    assert upstream.requests == []


def test_with_the_ner_on_a_name_is_masked_and_restored() -> None:
    ner, predictor = fake_detector(NAMES)
    with panel_client(HTTPS_URL, ner=ner) as client:
        session_id, token = logged_in(client)

        response = post_try(client, session_id, token, body(f"Soy {CARMEN}, DNI {SENTINEL_DNI}"))

    data = response.json()
    assert response.status_code == 200
    assert data["ner"] == "ok"
    assert [s for s in data["sent"] if "marker" in s] == [
        {"marker": "[[PERSON_1]]", "type": "PERSON"},
        {"marker": "[[ES_DNI_1]]", "type": "ES_DNI"},
    ]
    assert [s["value"] for s in data["reply"] if "marker" in s] == [CARMEN, SENTINEL_DNI]
    assert predictor.calls >= 1


class FailingPredictor(InProcess):
    def predict(self, *args: Any, **kwargs: Any) -> object:
        raise RuntimeError(f"the model broke on {SENTINEL_DNI}")


def test_a_failing_ner_blocks_with_nothing_sent() -> None:
    ner, _ = fake_detector(NAMES)
    ner._predictor = FailingPredictor(ner._predictor.backend)  # type: ignore[attr-defined]
    with panel_client(HTTPS_URL, ner=ner) as client:
        session_id, token = logged_in(client)

        response = post_try(client, session_id, token, body(f"Soy {CARMEN}"))

    data = response.json()
    assert response.status_code == 200
    assert (data["outcome"], data["blocked_reason"]) == ("blocked", "detector_failed")
    assert data["sent"] == [] and data["reply"] == [] and data["entities"] == []
    assert SENTINEL_DNI not in response.text and CARMEN not in response.text


def test_a_value_the_guard_still_sees_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    from antifaz.detect.types import Confidence, EntityType, Layer, Span

    def first_only(text: str) -> list[Span]:
        start = text.find("Zoraya")
        return [Span(start, start + 6, EntityType.PERSON, Layer.PATTERN, Confidence.HIGH)]

    with panel_client(HTTPS_URL, detector=first_only) as client:
        session_id, token = logged_in(client)

        response = post_try(client, session_id, token, body("Zoraya y Zoraya"))

    data = response.json()
    assert (data["outcome"], data["blocked_reason"]) == ("blocked", "egress_guard")
    assert data["sent"] == [] and data["reply"] == []
    assert "Zoraya" not in response.text


def test_the_entities_are_in_utf16_offsets() -> None:
    thumb = chr(0x1F44D)
    text = f"{thumb}{thumb} {SENTINEL_DNI}"
    with panel_client(HTTPS_URL) as client:
        session_id, token = logged_in(client)

        data = post_try(client, session_id, token, body(text)).json()

    assert data["length_utf16"] == 2 * 2 + 1 + len(SENTINEL_DNI)
    assert data["entities"] == [{"type": "ES_DNI", "start": 5, "end": 14, "hidden": True}]


def test_the_original_text_is_never_echoed() -> None:
    text = f"Escribo esto: {SENTINEL_DNI} y nada más"
    with panel_client(HTTPS_URL) as client:
        session_id, token = logged_in(client)

        response = post_try(client, session_id, token, body(text))

    assert text not in response.text
    assert "Escribo esto: [[ES_DNI_1]] y nada más" in "".join(
        s.get("text", s.get("marker", "")) for s in response.json()["sent"]
    )
