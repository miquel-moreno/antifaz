"""Settings, read from environment variables (and .env).

Upstream URLs and provider keys come ONLY from here, never from the client (ADR-0013).
`check_safe_to_start()` refuses a configuration that would run open or with the example keys
(ADR-0015). Its messages name the variable and the reason, never the value.
"""

import hmac
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from antifaz.trusted_networks import proxy_network_problem

# Shortest gateway key accepted at startup (32 characters, about 190 bits if random).
MIN_KEY_LENGTH = 32
# Fewer different characters than this ("aaaa...", "abab...") is not a random key.
MIN_DISTINCT_CHARACTERS = 8
# Every key in .env.example starts with "change-me": a copied example never starts. Compared
# without case, spaces, "-" or "_", so "change_me" and "ChangeMe" are caught too.
EXAMPLE_MARK = "changeme"
# "*.example.com": a wildcard must keep at least two labels after it ("*.com" is refused).
_SUBDOMAIN_WILDCARD = re.compile(r"\*\.[^.*]+\.[^.*]+(?:\.[^.*]+)*")
# An origin as browsers send it: scheme://host[:port], nothing after.
_ORIGIN = re.compile(
    r"https?://(?:[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?"
)
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

    # Token that opens the panel at /panel (ADR-0018). Unset: the panel does not exist. Same
    # rules as the gateway key, and it must differ from it and from the provider keys.
    admin_token: SecretStr | None = None
    # IPs or CIDR ranges of the reverse proxy in front of the panel (ADR-0018): only a request
    # from one of them may set X-Forwarded-For / X-Forwarded-Proto for the panel. Empty: none.
    trusted_proxies: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Names and addresses with a NER model in separate processes (ADR-0016). Off by default;
    # when on, the model in ner_model_dir must match detect/ner/manifest.json or the gateway
    # refuses to start (antifaz.detect.ner.setup).
    ner_enabled: bool = False
    ner_model_dir: Path | None = None
    ner_timeout_seconds: float = Field(default=10.0, gt=0, le=600)
    ner_workers: int = Field(default=1, ge=1, le=64)
    ner_threshold: float = Field(default=0.5, gt=0, le=1)
    ner_cache_entries: int = Field(default=10_000, ge=0, le=1_000_000)
    # CPU threads of torch in EACH worker process; 0 lets torch choose (all cores). With several
    # workers, workers x threads should not pass the number of cores.
    ner_torch_threads: int = Field(default=0, ge=0, le=256)

    @field_validator("allowed_hosts", "allowed_origins", "trusted_proxies", mode="before")
    @classmethod
    def _split_list(cls, value: object) -> object:
        """A JSON list ('[ "a", "b" ]') or a comma-separated one ("a, b", "[::1],localhost")."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None  # "[::1],localhost" is not JSON: read it as a comma list
            if isinstance(parsed, list):
                return parsed
        return [item.strip() for item in text.split(",") if item.strip()]

    @field_validator("allowed_hosts", mode="after")
    @classmethod
    def _lower_hosts(cls, hosts: list[str]) -> list[str]:
        return [host.lower() for host in hosts]  # host names have no case; Host is lowered too


def _is_example(key: str) -> bool:
    """The .env.example value, also written "change_me", "ChangeMe" or "CHANGE-ME"."""
    return re.sub(r"[\s_-]", "", key.casefold()).startswith(EXAMPLE_MARK)


def _format_problem(key: str) -> str | None:
    """Why `key` cannot travel as a key (gateway or provider), or None if it can."""
    if not key:
        return "is empty: set a value or remove the variable"
    if _is_example(key):
        return "still has the example value from .env.example: set your own random value"
    if not (key.isascii() and key.isprintable()) or " " in key:
        return "must be printable ASCII without spaces (it travels in an HTTP header)"
    return None


def _gateway_key_problem(key: str) -> str | None:
    """Why `key` cannot be the gateway key, or None if it can."""
    if not key:
        return "is missing: set a random value of at least 32 characters"
    problem = _format_problem(key)
    if problem:
        return problem
    if len(key) < MIN_KEY_LENGTH:
        return f"is too short: use at least {MIN_KEY_LENGTH} characters"
    if len(set(key)) < MIN_DISTINCT_CHARACTERS:
        return (
            f"has fewer than {MIN_DISTINCT_CHARACTERS} different characters: "
            "use a random value (for example: openssl rand -hex 32)"
        )
    return None


def _host_problem(host: str) -> bool:
    """True for an empty host, "*" or a wildcard broader than "*.<two labels>"."""
    if not host:
        return True
    return "*" in host and not _SUBDOMAIN_WILDCARD.fullmatch(host)


def _admin_token_problem(token: str) -> str | None:
    """Why `token` cannot be the admin token, or None if it can (same rules as the gateway key).

    An empty value is a mistake, not "no panel": to turn the panel off, the variable goes."""
    if not token:
        return "is empty: set a value or remove the variable"
    return _gateway_key_problem(token)


def _same_secret(first: str, second: str) -> bool:
    return hmac.compare_digest(first.encode(), second.encode())


def _check_admin_token(settings: Settings, gateway_key: str) -> None:
    if settings.admin_token is None:
        return
    token = settings.admin_token.get_secret_value()
    problem = _admin_token_problem(token)
    if problem:
        raise UnsafeConfigError(f"ANTIFAZ_ADMIN_TOKEN {problem}")
    if _same_secret(token, gateway_key):
        raise UnsafeConfigError(
            "ANTIFAZ_ADMIN_TOKEN must differ from ANTIFAZ_API_KEY: the API key must not "
            "open the panel"
        )
    for variable, secret in (
        ("ANTIFAZ_OPENAI_API_KEY", settings.openai_api_key),
        ("ANTIFAZ_ANTHROPIC_API_KEY", settings.anthropic_api_key),
    ):
        if secret is not None and _same_secret(token, secret.get_secret_value()):
            raise UnsafeConfigError(f"ANTIFAZ_ADMIN_TOKEN must differ from {variable}")


def check_safe_to_start(settings: Settings) -> None:
    """Raise UnsafeConfigError if the gateway would start open. Messages never carry values."""
    gateway_key = settings.antifaz_api_key.get_secret_value() if settings.antifaz_api_key else ""
    problem = _gateway_key_problem(gateway_key)
    if problem:
        raise UnsafeConfigError(f"ANTIFAZ_API_KEY {problem}")
    for variable, secret in (
        ("ANTIFAZ_OPENAI_API_KEY", settings.openai_api_key),
        ("ANTIFAZ_ANTHROPIC_API_KEY", settings.anthropic_api_key),
    ):
        problem = None if secret is None else _format_problem(secret.get_secret_value())
        if problem:
            raise UnsafeConfigError(f"{variable} {problem}")
    if not settings.allowed_hosts or any(_host_problem(h) for h in settings.allowed_hosts):
        raise UnsafeConfigError(
            "ANTIFAZ_ALLOWED_HOSTS must list the host names clients use; '*' is not accepted "
            "and a wildcard needs two labels after it ('*.example.com')"
        )
    if not all(_ORIGIN.fullmatch(origin) for origin in settings.allowed_origins):
        raise UnsafeConfigError(
            "ANTIFAZ_ALLOWED_ORIGINS only accepts exact origins written as scheme://host[:port] "
            "(http or https, no path and no trailing slash); '*' and 'null' are not accepted"
        )
    _check_admin_token(settings, gateway_key)
    for entry in settings.trusted_proxies:
        problem = proxy_network_problem(entry)  # a fixed message: never the entry itself
        if problem:
            raise UnsafeConfigError(f"ANTIFAZ_TRUSTED_PROXIES {problem}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
