"""Settings, read from environment variables (and .env)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "antifaz"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
