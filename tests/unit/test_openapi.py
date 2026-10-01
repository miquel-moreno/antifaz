"""The static OpenAPI file (docs/openapi.json) matches the app, which still does not serve it."""

import json
from pathlib import Path

import pytest
from scripts import export_openapi

from tests.integration.fakes import GATEWAY_KEY, FakeUpstream, models_list
from tests.redteam.conftest import gateway_client


def test_the_committed_file_is_the_generated_one() -> None:
    # If this fails, run `make openapi` and commit docs/openapi.json.
    committed = export_openapi.OUTPUT.read_text(encoding="utf-8")

    assert committed == export_openapi.render()


def test_the_file_documents_every_route() -> None:
    schema = json.loads(export_openapi.render())

    assert sorted(schema["paths"]) == [
        "/antifaz/scan",
        "/healthz",
        "/v1/chat/completions",
        "/v1/messages",
        "/v1/messages/count_tokens",
        "/v1/models",
    ]
    scan = schema["paths"]["/antifaz/scan"]["post"]
    assert "requestBody" in scan
    assert "400" in scan["responses"]
    models = schema["paths"]["/v1/models"]["get"]
    assert {p["name"] for p in models["parameters"]} >= {"after_id", "before_id", "limit"}


def test_the_file_needs_no_secret_and_carries_none() -> None:
    text = export_openapi.render()

    assert "api_key" not in text.lower().replace("x-api-key", "")
    assert GATEWAY_KEY not in text


def test_main_writes_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "openapi.json"
    monkeypatch.setattr(export_openapi, "OUTPUT", target)

    assert export_openapi.main() == 0
    raw = target.read_bytes()
    assert raw == export_openapi.render().encode("utf-8")
    assert raw.endswith(b"\n") and not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_the_app_still_serves_no_docs_even_with_the_key(path: str) -> None:
    for client in gateway_client(FakeUpstream(models_list)):
        response = client.get(path, headers={"Authorization": f"Bearer {GATEWAY_KEY}"})

        assert response.status_code == 404
