"""The «Estado» view never shows a secret or a personal value (issue 53, PR 11c-3b, ADR-0018).

Invariant 13: with canary values for the gateway key, the admin token, both provider keys and
base URLs that carry a user part, a path and a query
(`https://user:<canary>@proxy.example/v1/<canary>?key=<canary>`), neither GET
/panel/api/status nor the panel page (es, en) nor the logs at DEBUG hold any canary, nor ANY
substring of six characters or more of one of them.

Invariant 15: after the sentinel DNI went through the proxy (fake upstream), /antifaz/scan, a
refused request and a header, the status JSON and the panel page hold neither the sentinel nor a
marker of its type.

Structural: every leaf of the JSON is a boolean, an integer, one of the allowed enums, the
version (VERSION_PATTERN) or a host (HOST).

The canaries are fixed and use letters outside hexadecimal, so a hashed asset name or a CSRF
token can never match one of their fragments by chance.
"""

import json
import logging
import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from antifaz.web.status import HOST, NER_STATES, VERSION_PATTERN
from tests.conftest import SENTINEL_DNI
from tests.integration import test_proxy_anthropic as anth
from tests.integration import test_proxy_openai as oai
from tests.integration.fakes import FakeUpstream
from tests.integration.panel_client import HTTPS_URL, LOCAL_URL, cookie_of, log_in, panel_client
from tests.redteam.conftest import gateway_settings

GATEWAY_CANARY = "QzGateVxJmPtRnLsYhGfDcBaWqUoIeKyHw"
ADMIN_CANARY = "QzAdmnXvKpTrWjMsYgHnLqRtZuVoPiYkGw"
OPENAI_CANARY = "sk-QzOpnWvXkMtRpLsYjGhNqZuVoPiYkGx"
ANTHROPIC_CANARY = "sk-ant-QzAntWvXkMtRpLsYjGhNqZuVoPj"
PASSWORD = "CanaryPass"
PATH_PART = "CanaryPath"
QUERY = "CanaryQuery"
OPENAI_URL = f"https://user:{PASSWORD}@proxy.example/v1/{PATH_PART}?key={QUERY}"
ANTHROPIC_URL = f"https://user:{PASSWORD}@proxy-two.example:8443/{PATH_PART}?key={QUERY}"
SECRETS = (
    GATEWAY_CANARY,
    ADMIN_CANARY,
    OPENAI_CANARY,
    ANTHROPIC_CANARY,
    PASSWORD,
    PATH_PART,
    QUERY,
)
MIN_FRAGMENT = 6
STATUS = "/panel/api/status"
OVERALL = {"ok", "warn", "danger"}
SCHEMES = {"https", "http_localhost"}
PROVIDERS = {"openai", "anthropic"}


def _fragments(secret: str) -> set[str]:
    """Every substring of `secret` of MIN_FRAGMENT characters or more."""
    return {
        secret[start:end]
        for start in range(len(secret))
        for end in range(start + MIN_FRAGMENT, len(secret) + 1)
    }


FRAGMENTS = frozenset().union(*(_fragments(secret) for secret in SECRETS))


def _leaks(text: str) -> list[str]:
    return sorted(fragment for fragment in FRAGMENTS if fragment in text)


def _upstream(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/chat/completions"):
        return oai.echo(request)
    return anth.echo(request)


def _canary_settings() -> Any:
    return gateway_settings(
        antifaz_api_key=SecretStr(GATEWAY_CANARY),
        admin_token=SecretStr(ADMIN_CANARY),
        openai_api_key=SecretStr(OPENAI_CANARY),
        anthropic_api_key=SecretStr(ANTHROPIC_CANARY),
        openai_base_url=OPENAI_URL,
        anthropic_base_url=ANTHROPIC_URL,
        allowed_hosts=["testserver", "localhost", "antifaz.example"],
        log_level="DEBUG",
    )


def _admin_log_in(client: TestClient) -> str:
    origin = f"{client.base_url.scheme}://{client.base_url.netloc.decode('ascii')}"
    response = client.post(
        "/panel/login",
        json={"token": ADMIN_CANARY},
        headers={"Sec-Fetch-Site": "same-origin", "Origin": origin},
    )
    client.cookies.clear()
    assert response.status_code == 200
    return response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]


@pytest.fixture(params=[HTTPS_URL, LOCAL_URL])
def canary_client(request: pytest.FixtureRequest) -> Iterator[TestClient]:
    upstream = FakeUpstream(_upstream)
    with panel_client(request.param, settings=_canary_settings(), upstream=upstream) as client:
        yield client


def _panel_texts(client: TestClient, session_id: str) -> list[str]:
    cookie = {"Cookie": f"{cookie_of(client)}={session_id}"}
    status = client.get(STATUS, headers=cookie)
    assert status.status_code == 200
    texts = [status.text, json.dumps(dict(status.headers))]
    for lang in ("es", "en"):
        page = client.get(f"/panel?lang={lang}", headers=cookie)
        assert page.status_code == 200
        texts += [page.text, json.dumps(dict(page.headers))]
    return texts


def test_no_secret_and_no_fragment_of_one_in_the_status_the_page_or_the_logs(
    canary_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    session_id = _admin_log_in(canary_client)

    texts = _panel_texts(canary_client, session_id)

    body = json.loads(texts[0])
    assert [p["address"] for p in body["providers"]] == ["proxy.example", "proxy-two.example:8443"]
    assert [p["has_credentials"] for p in body["providers"]] == [True, True]
    assert _leaks("\n".join(texts)) == []
    assert caplog.records  # the log check below is not vacuous
    assert _leaks(caplog.text) == []
    assert _leaks("\n".join(str(record.__dict__) for record in caplog.records)) == []


def test_the_fragment_check_sees_a_leak() -> None:
    assert _leaks(f"x{PASSWORD[2:9]}y") != []
    assert _leaks(f"{OPENAI_CANARY[10:16]}") != []
    assert _leaks("proxy.example user v1 key ok") == []


def _send_the_sentinel_everywhere(client: TestClient) -> None:
    auth = {"Authorization": f"Bearer {GATEWAY_CANARY}"}
    text = f"Mi DNI es {SENTINEL_DNI}"
    chat = {"model": "m", "messages": [{"role": "user", "content": text}]}
    message = {"model": "m", "max_tokens": 8, "messages": [{"role": "user", "content": text}]}
    sent = [
        client.post("/v1/chat/completions", json=chat, headers=auth),
        client.post(
            "/v1/messages", json=message, headers={**auth, "anthropic-version": "2023-06-01"}
        ),
        client.post("/antifaz/scan", json={"text": text}, headers=auth),
    ]
    assert [response.status_code for response in sent] == [200, 200, 200]
    refused = client.post("/antifaz/scan", json={"text": text, "x": SENTINEL_DNI}, headers=auth)
    assert refused.status_code >= 400
    client.get("/v1/models", headers={**auth, "X-Note": SENTINEL_DNI})
    client.get(f"/panel/api/status?dni={SENTINEL_DNI}", headers={"X-Note": SENTINEL_DNI})


def test_the_sentinel_dni_never_reaches_the_status_nor_the_page(
    canary_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    session_id = _admin_log_in(canary_client)
    _send_the_sentinel_everywhere(canary_client)
    assert caplog.records  # the gateway logged while the sentinel went through

    texts = "\n".join(_panel_texts(canary_client, session_id))

    assert SENTINEL_DNI not in texts
    assert "[[ES_DNI_" not in texts
    assert "ES_DNI" not in texts


# --- structural: nothing in the JSON can be a free text --------------------------------------


def _leaves(node: object, path: str = "") -> Iterator[tuple[str, object]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, f"{path}.{key}")
    elif isinstance(node, list):
        for value in node:
            yield from _leaves(value, f"{path}[]")
    else:
        yield path, node


ENUMS = {
    ".detectors.validators": {"on"},
    ".detectors.patterns": {"on"},
    ".detectors.ner": NER_STATES,
    ".panel_scheme": SCHEMES,
    ".overall": OVERALL,
    ".providers[].id": PROVIDERS,
}


def _allowed(path: str, value: object) -> bool:
    if isinstance(value, bool):
        return path.endswith(("has_key", "has_credentials"))
    if isinstance(value, int):
        return value >= 0
    if not isinstance(value, str):
        return False
    if path in ENUMS:
        return value in ENUMS[path]
    if path == ".version":
        return re.fullmatch(VERSION_PATTERN, value) is not None
    if path == ".providers[].address":
        return value in ("default", "custom") or HOST.fullmatch(value) is not None
    return False


def test_every_leaf_of_the_json_is_a_bool_an_int_an_enum_the_version_or_a_host(
    canary_client: TestClient,
) -> None:
    session_id = _admin_log_in(canary_client)
    body = canary_client.get(STATUS, headers={"Cookie": f"{cookie_of(canary_client)}={session_id}"})

    leaves = list(_leaves(body.json()))
    assert len(leaves) >= 15
    assert [(path, value) for path, value in leaves if not _allowed(path, value)] == []


def test_the_leaf_check_refuses_free_text() -> None:
    assert not _allowed(".version", "0.1.1 sk-x")
    assert not _allowed(".providers[].address", "https://proxy.example/v1")
    assert not _allowed(".overall", "fine")
    assert not _allowed(".somewhere", "text")
    assert _allowed(".providers[].address", "[::1]:11434")


def test_a_normal_client_logs_in_with_the_test_helper_too() -> None:
    """The privacy fixture uses its own canary admin token; the shared helper still works."""
    with panel_client(HTTPS_URL) as client:
        assert client.get(
            STATUS, headers={"Cookie": f"{cookie_of(client)}={log_in(client)}"}
        ).json()
