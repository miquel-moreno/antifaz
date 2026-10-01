# Antifaz gateway image (issue 7, part a). Multi-stage: uv builds a virtualenv in the first
# stage; the final stage holds only Python and that virtualenv, runs as a fixed non-root user
# and writes nothing at runtime, so it works with a read-only root filesystem.
#
# No secrets ever go into the image: every key comes from the environment at runtime
# (compose reads them from .env). .dockerignore only lets in what the build needs.
#
# Default image: no NER (the `ner` extra adds torch, ~1 GB). A separate `-ner` tag with
# `uv sync --extra ner` and the model mounted read-only is planned for later.

# Base images pinned by digest (a tag can be moved; a digest cannot). The FROM lines are written
# out in full (no ARG) so Dependabot can update them; the two python ones must stay equal.
#   python:3.12-slim = 3.12.14-slim-trixie, index digest resolved on 2026-10-01 with
#   `docker buildx imagetools inspect python:3.12-slim`.
#   ghcr.io/astral-sh/uv:0.12.20 (the uv version CI uses), index digest resolved on 2026-10-01.
FROM ghcr.io/astral-sh/uv:0.12.20@sha256:100047e74f30778ab704942321a09750d6158739573ff58bf3924085cc6cd2d8 AS uv

# ---- Build stage: the virtualenv with the locked runtime dependencies (no dev, no extras) ----
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS build

COPY --from=uv /uv /usr/local/bin/uv

# Bytecode compiled now (nothing is written at runtime); the Python of the base image, never a
# downloaded one; files copied, not linked, so the venv can move to the final stage.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src

# Dependencies first (their layer is cached while only the code changes), exactly as uv.lock says.
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Then Antifaz itself, installed as a package (not editable): the final image needs no source tree.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---- Final stage ----
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS runtime

LABEL org.opencontainers.image.title="antifaz" \
      org.opencontainers.image.description="Privacy gateway for LLMs: pseudonymises Spanish and EU personal data before it reaches the provider." \
      org.opencontainers.image.source="https://github.com/miquel-moreno/antifaz" \
      org.opencontainers.image.licenses="Apache-2.0"

# A fixed, unprivileged user with no home and no shell. Fixed UID/GID so volumes and
# Kubernetes securityContext can rely on it.
RUN groupadd --system --gid 10001 antifaz \
    && useradd --system --uid 10001 --gid 10001 --no-create-home \
       --home-dir /nonexistent --shell /usr/sbin/nologin antifaz

# Owned by root and not writable by the user that runs the gateway.
COPY --from=build /app/.venv /app/.venv

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
USER 10001:10001
EXPOSE 8000

# Python, not curl: the image ships no HTTP tools. See src/antifaz/healthcheck.py.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-m", "antifaz.healthcheck"]

# --factory: the app is built at start, and a refused configuration (ADR-0015) stops it.
# --no-proxy-headers: X-Forwarded-* are ignored; behind a reverse proxy see docs/TECNICO.md.
# --no-server-header: the answers do not say which server runs the gateway.
ENTRYPOINT ["uvicorn", "--factory", "antifaz.api.app:create_app", \
            "--host", "0.0.0.0", "--port", "8000", \
            "--no-access-log", "--no-proxy-headers", "--no-server-header", \
            "--timeout-graceful-shutdown", "20"]
