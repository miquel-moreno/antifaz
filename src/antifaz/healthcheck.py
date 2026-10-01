"""Container healthcheck: `python -m antifaz.healthcheck` exits 0 if /healthz answers "ok".

The image has no curl or wget (fewer tools for an attacker), so Docker runs this instead. It
asks the gateway inside the same container (127.0.0.1:8000) with a Host header taken from
ANTIFAZ_ALLOWED_HOSTS, so the trusted-host check accepts it whatever names the admin set.
It reads only the environment (never a .env file) and prints nothing but a short reason.
"""

import json
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence

from antifaz.config import Settings

URL = "http://127.0.0.1:8000/healthz"
TIMEOUT_SECONDS = 3.0


def host_header(allowed_hosts: Sequence[str]) -> str:
    """A Host value the gateway accepts: the first allowed host ("*.x.y" -> "healthcheck.x.y")."""
    if not allowed_hosts:
        return "127.0.0.1"
    return allowed_hosts[0].replace("*", "healthcheck", 1)


def main() -> int:
    try:
        hosts = Settings(_env_file=None).allowed_hosts
        request = urllib.request.Request(URL, headers={"Host": host_header(hosts)})
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            body = json.loads(response.read(4096))
            healthy = response.status == 200 and body.get("status") == "ok"
    except (OSError, ValueError, AttributeError):
        healthy = False
    if not healthy:
        print("antifaz healthcheck: not healthy", file=sys.stderr)
    return 0 if healthy else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
