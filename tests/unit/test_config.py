import pytest

from antifaz.config import Settings


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    # _env_file is a pydantic-settings init option that mypy does not see in the signature.
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.log_level == "DEBUG"
    assert settings.app_name == "antifaz"
