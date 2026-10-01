"""Write the OpenAPI schema of the gateway to docs/openapi.json (`make openapi`).

The running gateway serves no /docs, /redoc or /openapi.json (ADR-0015): the schema is
published as a static file instead. It is generated from the app itself, built with a random
key that is thrown away (the schema holds no setting). tests/unit/test_openapi.py checks
that the committed file is the generated one, so it never drifts from the code.

    uv run python -m scripts.export_openapi
"""

import json
import secrets
from pathlib import Path

from pydantic import SecretStr

from antifaz.api.app import create_app
from antifaz.config import Settings

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "openapi.json"


def render() -> str:
    """The schema as JSON text: sorted keys, two spaces, a final newline."""
    settings = Settings(
        antifaz_api_key=SecretStr(secrets.token_hex(32)),
        app_name="antifaz",
        ner_enabled=False,
        _env_file=None,  # type: ignore[call-arg]
    )
    schema = create_app(settings).openapi()
    return json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main() -> int:
    OUTPUT.write_bytes(render().encode("utf-8"))  # bytes: LF and no BOM on every system
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
