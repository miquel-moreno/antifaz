"""The e2e stack never reaches a real provider: the pieces that make sure of it.

Three layers. The gateway and the fake provider live on a Compose network with `internal: true`
(no way out), and a relay on a second network is the only door from the host
(tests/e2e/docker-compose.e2e.yml, tests/e2e/relay.py). `docker run` in the tests uses
`--network none` (tests/unit/test_e2e_egress.py checks it). And before Compose starts, the
throwaway env file must point both providers at the fake one (`upstream_problems`).
"""

from collections.abc import Mapping
from urllib.parse import urlsplit

FAKE_UPSTREAM_HOST = "fake-upstream"
FAKE_UPSTREAM_PORT = 9000
# The Compose network with no egress (its key in docker-compose.e2e.yml).
INTERNAL_NETWORK = "e2e-internal"
UPSTREAM_VARS = ("ANTIFAZ_OPENAI_BASE_URL", "ANTIFAZ_ANTHROPIC_BASE_URL")


def read_env(text: str) -> dict[str, str]:
    """`NAME=value` lines of an env file (blank lines and `#` comments skipped)."""
    env: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        env[name.strip()] = value
    return env


def _is_fake_upstream(url: str) -> bool:
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return False
    return (
        url == url.strip()
        and parts.scheme == "http"
        and parts.username is None
        and parts.password is None
        and parts.hostname == FAKE_UPSTREAM_HOST
        and port == FAKE_UPSTREAM_PORT
    )


def upstream_problems(env: Mapping[str, str]) -> list[str]:
    """One message per provider base URL that does not point at the fake provider.

    A missing variable counts: Antifaz would fall back to the real provider. Messages name the
    variable, never its value.
    """
    target = f"plain http, host {FAKE_UPSTREAM_HOST}, port {FAKE_UPSTREAM_PORT}"
    return [
        f"{name} must point at the fake provider ({target}) during the e2e tests"
        for name in UPSTREAM_VARS
        if not _is_fake_upstream(env.get(name, ""))
    ]
