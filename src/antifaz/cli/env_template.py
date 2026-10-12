"""The .env that `antifaz init` writes (issue 41), embedded so the published image has it too.

The same variables, in the same order, as .env.example (a test keeps them in sync); the
values of ANTIFAZ_API_KEY, ANTIFAZ_ADMIN_TOKEN, the provider keys and ANTIFAZ_ALLOWED_HOSTS
are filled in by cli/init.py, and a provider without a key loses its line.

Written for pydantic-settings AND `docker --env-file` at once: one VAR=VALUE per line, no
quotes, no comment after a value and no `$` anywhere (Compose would expand it).
"""

ENV_TEMPLATE = """\
# Written by `antifaz init`. Never commit this file: it holds your keys.
# Every variable starts with ANTIFAZ_ so it never mixes with the ones Claude Code or the SDKs
# use (ANTHROPIC_BASE_URL, OPENAI_API_KEY...) in the same shell.
# Keep this format: one VAR=VALUE per line, no quotes and no comment after a value.
ANTIFAZ_APP_NAME=antifaz
ANTIFAZ_LOG_LEVEL=INFO

# Key your clients send as "Authorization: Bearer ..." or "x-api-key".
# Random, made on this machine by antifaz init. Run init again to replace it.
ANTIFAZ_API_KEY=
# Providers. The URLs and the keys are only read from here, never from the client.
# A provider without a key answers 503 on its route.
ANTIFAZ_OPENAI_BASE_URL=https://api.openai.com/v1
ANTIFAZ_OPENAI_API_KEY=
# Anthropic, without "/v1" at the end.
ANTIFAZ_ANTHROPIC_BASE_URL=https://api.anthropic.com
ANTIFAZ_ANTHROPIC_API_KEY=

# Host names clients use to reach Antifaz (the Host header), comma-separated, any case.
# "*" is refused; a wildcard needs two labels after it ("*.example.com").
# "antifaz" is the Compose service name (other containers reach it at http://antifaz:8000).
# Behind a reverse proxy, add the name clients use (e.g. antifaz.internal).
ANTIFAZ_ALLOWED_HOSTS=localhost,127.0.0.1,[::1],antifaz
# Browser origins allowed on the proxy routes, comma-separated, as scheme://host[:port]
# (no path, no trailing slash). Empty: every request with an Origin header is refused (403).
# "*" and "null" are refused. There is no CORS either way.
ANTIFAZ_ALLOWED_ORIGINS=

# Token that opens the panel in the browser at /panel (from v0.2). Random, made by antifaz
# init, different from ANTIFAZ_API_KEY. To turn the panel off, remove this line and restart.
ANTIFAZ_ADMIN_TOKEN=
# Only behind a reverse proxy: the EXACT IP of the proxy (e.g. 172.20.0.10), comma-separated.
# Never the Docker gateway or the whole Docker network: any client could fake the headers.
# Only a request from one of them may set X-Forwarded-For / X-Forwarded-Proto for the panel.
ANTIFAZ_TRUSTED_PROXIES=

ANTIFAZ_UPSTREAM_TIMEOUT_SECONDS=120
ANTIFAZ_UPSTREAM_CONNECT_TIMEOUT_SECONDS=10
ANTIFAZ_MAX_BODY_BYTES=4194304

# Names and addresses with a NER model in separate processes (ADR-0016). Off by default.
# With true, Antifaz refuses to start unless ANTIFAZ_NER_MODEL_DIR holds exactly the model of
# the manifest (sizes and SHA-256) and the antifaz[ner] extra is installed.
ANTIFAZ_NER_ENABLED=false
# ANTIFAZ_NER_MODEL_DIR=models/gliner_multi_pii-v1
# Time limit of the NER for a whole request; past it the worker is killed and the request blocked.
ANTIFAZ_NER_TIMEOUT_SECONDS=10
# Worker processes; each one holds a copy of the model in memory.
ANTIFAZ_NER_WORKERS=1
ANTIFAZ_NER_THRESHOLD=0.5
# Texts kept in the span cache (offsets and types only, never text). 0 turns it off.
ANTIFAZ_NER_CACHE_ENTRIES=10000
# CPU threads of torch in each worker; 0 lets torch choose (all cores).
ANTIFAZ_NER_TORCH_THREADS=0
"""

# Filled in by init; a provider key left empty removes its line.
API_KEY = "ANTIFAZ_API_KEY"
ADMIN_TOKEN = "ANTIFAZ_ADMIN_TOKEN"  # noqa: S105 - the variable name, not a token
OPENAI_KEY = "ANTIFAZ_OPENAI_API_KEY"
ANTHROPIC_KEY = "ANTIFAZ_ANTHROPIC_API_KEY"
ALLOWED_HOSTS = "ANTIFAZ_ALLOWED_HOSTS"
DEFAULT_ALLOWED_HOSTS = "localhost,127.0.0.1,[::1],antifaz"

__all__ = [
    "ADMIN_TOKEN",
    "ALLOWED_HOSTS",
    "ANTHROPIC_KEY",
    "API_KEY",
    "DEFAULT_ALLOWED_HOSTS",
    "ENV_TEMPLATE",
    "OPENAI_KEY",
]
