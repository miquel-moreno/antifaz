"""`antifaz serve`: run the gateway with uvicorn, as the image did before (issue 42, ADR-0017).

The image runs `ENTRYPOINT ["antifaz"]` with `CMD ["serve"]`, so the same image also runs
`init` and `verify`. The settings are the ones of the old ENTRYPOINT and take no options:

- `factory`: the app is built at start, so a refused configuration (ADR-0015) stops it;
- `host 0.0.0.0`, port 8000: inside the container (Compose publishes it on 127.0.0.1 only);
- no uvicorn access log (it would hold paths and queries), no `X-Forwarded-*` (see "Proxy
  inverso" in docs/TECNICO.md), no `Server` header, 20 s to finish requests on shutdown.

They go to `uvicorn.run`, which (unlike the `uvicorn` command line) reads no `UVICORN_*`
variable: nothing in .env can change them. tests/unit/test_cli_serve.py pins them.
"""

from typing import Any

APP = "antifaz.api.app:create_app"
OPTIONS: dict[str, Any] = {
    "factory": True,
    "host": "0.0.0.0",  # noqa: S104 - inside the container; published on 127.0.0.1 by Compose
    "port": 8000,
    "access_log": False,
    "proxy_headers": False,
    "server_header": False,
    "timeout_graceful_shutdown": 20,
}


def run() -> int:
    # Imported here: scan, mask and init do not need the web server.
    import uvicorn

    uvicorn.run(APP, **OPTIONS)
    return 0


__all__ = ["APP", "OPTIONS", "run"]
