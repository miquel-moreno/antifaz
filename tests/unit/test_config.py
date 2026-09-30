import pytest

from antifaz.config import Settings


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTIFAZ_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("ANTIFAZ_API_KEY", "test-gateway-key-not-real")
    monkeypatch.setenv("ANTIFAZ_ANTHROPIC_API_KEY", "test-provider-key-not-real")
    monkeypatch.setenv("ANTIFAZ_OPENAI_BASE_URL", "https://openai.invalid/v1")

    # _env_file is a pydantic-settings init option that mypy does not see in the signature.
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.log_level == "DEBUG"
    assert settings.app_name == "antifaz"
    assert settings.antifaz_api_key is not None
    assert settings.antifaz_api_key.get_secret_value() == "test-gateway-key-not-real"
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "test-provider-key-not-real"
    assert settings.openai_base_url == "https://openai.invalid/v1"


def test_client_sdk_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    # A shell set up for Claude Code or the SDKs must not make Antifaz call itself.
    for name, value in {
        "ANTHROPIC_API_KEY": "test-gateway-key-not-real",
        "ANTHROPIC_BASE_URL": "http://localhost:8000",
        "OPENAI_API_KEY": "test-gateway-key-not-real",
        "OPENAI_BASE_URL": "http://localhost:8000/v1",
        "LOG_LEVEL": "DEBUG",
        "MAX_BODY_BYTES": "1",
    }.items():
        monkeypatch.setenv(name, value)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.anthropic_api_key is None
    assert settings.anthropic_base_url == "https://api.anthropic.com"
    assert settings.openai_api_key is None
    assert settings.openai_base_url == "https://api.openai.com/v1"
    assert settings.log_level == "INFO"
    assert settings.max_body_bytes == 4 * 1024 * 1024
