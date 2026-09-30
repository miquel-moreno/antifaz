"""Settings, read from environment variables (and .env).

Upstream URLs and provider keys come ONLY from here, never from the client (ADR-0013).
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Key the clients send as "Authorization: Bearer ..." (or "x-api-key" on the Anthropic
    # routes). Without it the proxy refuses to work.
    antifaz_api_key: SecretStr | None = Field(default=None, validation_alias="ANTIFAZ_API_KEY")

    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: SecretStr | None = None

    # Without "/v1": the routes add it, as the official SDKs do with ANTHROPIC_BASE_URL.
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_api_key: SecretStr | None = None

    upstream_timeout_seconds: float = 120.0
    upstream_connect_timeout_seconds: float = 10.0
    max_body_bytes: int = 4 * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
