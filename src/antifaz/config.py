"""Settings, read from environment variables (and .env).

Upstream URLs and provider keys come ONLY from here, never from the client (ADR-0013).
`check_safe_to_start()` refuses a configuration that would run open or with the example keys
(ADR-0015). Its messages name the variable and the reason, never the value.
"""

import json
from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Shortest gateway key accepted at startup (32 characters, about 190 bits if random).
MIN_KEY_LENGTH = 32
# Every key in .env.example starts with this: a copied example never starts.
EXAMPLE_PREFIX = "change-me"
DEFAULT_ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]


class UnsafeConfigError(Exception):
    """The configuration would run the gateway open. The message never contains a value."""


class Settings(BaseSettings):
    # Every variable starts with ANTIFAZ_: a shell set up for Claude Code or the SDKs
    # (ANTHROPIC_BASE_URL, OPENAI_API_KEY...) must not make Antifaz call itself.
    model_config = SettingsConfigDict(
        env_prefix="ANTIFAZ_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "antifaz"
    log_level: str = "INFO"

    # Key the clients send as "Authorization: Bearer ..." or "x-api-key". Required to start.
    antifaz_api_key: SecretStr | None = Field(default=None, validation_alias="ANTIFAZ_API_KEY")

    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: SecretStr | None = None

    # Without "/v1": the routes add it, as the official SDKs do with ANTHROPIC_BASE_URL.
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_api_key: SecretStr | None = None

    upstream_timeout_seconds: float = 120.0
    upstream_connect_timeout_seconds: float = 10.0
    max_body_bytes: int = 4 * 1024 * 1024

    # Host header values accepted (TrustedHostMiddleware). Comma-separated or a JSON list.
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_HOSTS)
    )
    # Browser origins allowed on the proxy routes. Empty: every request with Origin is refused.
    allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("allowed_hosts", "allowed_origins", mode="before")
    @classmethod
    def _split_list(cls, value: object) -> object:
        """A comma-separated list ("a, b") or a JSON list ('["a", "b"]')."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text == "[]" or text.startswith('["'):  # JSON; "[::1],localhost" is not
            return json.loads(text)
        return [item.strip() for item in text.split(",") if item.strip()]


def _key_problem(key: str) -> str | None:
    """Why `key` cannot be the gateway key, or None if it can."""
    if not key:
        return "is missing: set a random value of at least 32 characters"
    if key.casefold().startswith(EXAMPLE_PREFIX):
        return "still has the example value from .env.example: set your own random value"
    if len(key) < MIN_KEY_LENGTH:
        return f"is too short: use at least {MIN_KEY_LENGTH} characters"
    if not (key.isascii() and key.isprintable()) or " " in key:
        return "must be printable ASCII without spaces (it travels in an HTTP header)"
    return None


def check_safe_to_start(settings: Settings) -> None:
    """Raise UnsafeConfigError if the gateway would start open. Messages never carry values."""
    gateway_key = settings.antifaz_api_key.get_secret_value() if settings.antifaz_api_key else ""
    problem = _key_problem(gateway_key)
    if problem:
        raise UnsafeConfigError(f"ANTIFAZ_API_KEY {problem}")
    for variable, secret in (
        ("ANTIFAZ_OPENAI_API_KEY", settings.openai_api_key),
        ("ANTIFAZ_ANTHROPIC_API_KEY", settings.anthropic_api_key),
    ):
        if secret is not None and secret.get_secret_value().casefold().startswith(EXAMPLE_PREFIX):
            raise UnsafeConfigError(f"{variable} still has the example value from .env.example")
    if not settings.allowed_hosts or any(h in ("", "*") for h in settings.allowed_hosts):
        raise UnsafeConfigError(
            "ANTIFAZ_ALLOWED_HOSTS must list the host names clients use; '*' is not accepted"
        )
    if any(o in ("", "*", "null") for o in settings.allowed_origins):
        raise UnsafeConfigError(
            "ANTIFAZ_ALLOWED_ORIGINS only accepts exact origins; '*' and 'null' are not accepted"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
