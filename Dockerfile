# Antifaz gateway image (issue 7, part a; distroless since issue 54, ADR-0019). Multi-stage: uv
# builds a virtualenv; a staging stage trims CPython 3.12 of the same official image; the final
# stage is distroless (no shell, no package manager, no HTTP tools) with only that Python and
# the virtualenv. It runs as a fixed non-root user and writes nothing at runtime, so it works
# with a read-only root filesystem.
#
# No secrets ever go into the image: every key comes from the environment at runtime
# (compose reads them from .env). .dockerignore only lets in what the build needs.
#
# Default image: no NER (the `ner` extra adds torch, ~1 GB). A separate `-ner` tag with
# `uv sync --extra ner` and the model mounted read-only is planned for later (it will need
# ctypes and libffi back: torch loads its libraries with ctypes; see ADR-0019).

# Base images pinned by digest (a tag can be moved; a digest cannot). The FROM lines are written
# out in full (no ARG) so Dependabot can update them. The two python ones must stay equal, and
# python (trixie) and distroless (debian13) must be the same Debian release: CPython is built
# against the C library of its image and runs on the C library of distroless
# (tests/unit/test_container_files.py checks both).
#   python:3.12-slim-trixie = python:3.12-slim = 3.12.15, index digest resolved on 2026-10-16
#   (the same as on 2026-10-09) with `docker buildx imagetools inspect python:3.12-slim-trixie`.
#   gcr.io/distroless/cc-debian13:nonroot (Debian 13.7), index digest resolved on 2026-10-16.
#   ghcr.io/astral-sh/uv:0.13.0 (the uv version CI uses), index digest resolved on 2026-10-16.
FROM ghcr.io/astral-sh/uv:0.13.3@sha256:bee0a2a458fd956c5df7b8b4c5fed2257b0af34866e3da7b51db0be3acb375d1 AS uv

# ---- Build stage: the virtualenv with the locked runtime dependencies (no dev, no extras) ----
FROM python:3.12-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS build

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

# ---- Python stage: the CPython 3.12 runtime that goes into the final image, trimmed ----
FROM python:3.12-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS python

# Removed (ADR-0019):
# - installers and tools the gateway never runs: pip, setuptools, wheel, ensurepip, IDLE,
#   tkinter/turtle and the CPython test modules;
# - the compiled modules whose system library distroless cc does not ship (libffi, sqlite,
#   liblzma, libbz2, readline, ncurses, gdbm, Berkeley DB, libuuid, libcrypt, Tk), with the
#   packages that only wrap them (ctypes, sqlite3, curses). No installed package needs them:
#   `make e2e` imports every module of the virtualenv (tests/e2e/test_image_imports.py). Each
#   library would also need its dpkg record, or the scanners would not see it.
# The rest links only libc, libm, libz and libssl/libcrypto (libzstd comes in through libcrypto,
# not from CPython itself), all of which distroless cc has.
# The `.cpython-312-x86_64-linux-gnu.so` suffix below is hard-coded: the image is built and
# tested for linux/amd64 only (release.yml); another platform needs its own suffix here.
RUN set -eu; \
    cd /usr/local/lib/python3.12; \
    rm -rf site-packages/pip site-packages/pip-*.dist-info \
           site-packages/setuptools site-packages/setuptools-*.dist-info \
           site-packages/wheel site-packages/wheel-*.dist-info \
           ensurepip idlelib tkinter turtledemo turtle.py ctypes sqlite3 curses; \
    for module in _bz2 _crypt _ctypes _curses _curses_panel _dbm _gdbm _lzma _sqlite3 _tkinter \
                  _uuid readline _ctypes_test _testbuffer _testcapi _testclinic \
                  _testimportmultiple _testinternalcapi _testmultiphase _testsinglephase \
                  _xxtestfuzz xxlimited xxlimited_35 xxsubtype; do \
        rm "lib-dynload/${module}.cpython-312-x86_64-linux-gnu.so"; \
    done

# ---- Final stage: distroless cc (glibc, libgcc, libstdc++, OpenSSL, zlib, zstd, CA certificates,
# tzdata), with a dpkg record for each package in /var/lib/dpkg/status.d so Syft and Grype list
# them. No RUN here: there is no shell. Debian security fixes arrive with a new digest of this
# base (Dependabot), not with apt-get.
FROM gcr.io/distroless/cc-debian13:nonroot@sha256:e792ab3d241a468a4fd7519ddbbebe66b49b5f365771716ea688ad40b6c6f1c2 AS runtime

LABEL org.opencontainers.image.title="antifaz" \
      org.opencontainers.image.description="Privacy gateway for LLMs: pseudonymises Spanish and EU personal data before it reaches the provider." \
      org.opencontainers.image.source="https://github.com/miquel-moreno/antifaz" \
      org.opencontainers.image.licenses="Apache-2.0"

# CPython where the virtualenv expects it (its pyvenv.cfg says home = /usr/local/bin and its
# `python` links to /usr/local/bin/python3.12; the binary finds libpython in ../lib). Syft
# identifies CPython by these two files. Owned by root and not writable by the gateway's user,
# like the virtualenv.
COPY --from=python /usr/local/bin/python3.12 /usr/local/bin/python3.12
COPY --from=python /usr/local/lib/libpython3.12.so.1.0 /usr/local/lib/libpython3.12.so.1.0
COPY --from=python /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=build /app/.venv /app/.venv

# LANG as in the official Python image (distroless sets none): UTF-8 everywhere. SSL_CERT_FILE
# is what distroless sets today, declared here so a new base cannot drop it silently (the
# default TLS context would trust nothing; make e2e counts the CA certificates).
ENV PATH=/app/.venv/bin:$PATH \
    LANG=C.UTF-8 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
# A fixed, unprivileged UID/GID (not the base's 65532) so volumes and Kubernetes securityContext
# can rely on it, as before. It has no entry in /etc/passwd: nothing in the gateway needs one,
# and there is no shell to log in with anyway.
USER 10001:10001
EXPOSE 8000

# Python, not curl, in exec form: the image ships no HTTP tools and no shell. See
# src/antifaz/healthcheck.py.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-m", "antifaz.healthcheck"]

# One command for everything (ADR-0017): `docker run IMAGE` serves, and the same image runs
# `docker run --rm -it -v "$PWD:/work" -w /work IMAGE init` or `... IMAGE verify`.
# `antifaz serve` runs uvicorn with the settings this ENTRYPOINT had up to v0.1.0, fixed in
# src/antifaz/cli/serve.py: --factory (a refused configuration stops it), 0.0.0.0:8000, no
# access log, no X-Forwarded-* (docs/TECNICO.md), no Server header, 20 s graceful shutdown.
ENTRYPOINT ["antifaz"]
CMD ["serve"]
