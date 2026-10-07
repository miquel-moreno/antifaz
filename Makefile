# Common tasks. Run `make help` to list them.
PKG := antifaz
UV_AUDIT := uvx --from uv==0.12.20 uv audit --frozen --preview-features audit-command
ZIZMOR := uvx zizmor==1.30.1 --offline --persona auditor --format plain

.PHONY: help install dev up down lint format typecheck test contract e2e secrets check audit licenses bench workflows ner-model openapi demo design-check

help:
	@echo "install    Install dependencies and git hooks"
	@echo "dev        Run the API with auto-reload on http://localhost:8000"
	@echo "up         Build the image from this clone and start it with Compose (needs .env)"
	@echo "down       Stop what make up started"
	@echo "lint       Check style and common bugs (ruff)"
	@echo "format     Auto-format the code (ruff)"
	@echo "typecheck  Check types (mypy strict)"
	@echo "test       Tests with coverage (80 % overall, 90 % in the privacy pieces)"
	@echo "contract   Only the contract tests: official SDKs against Antifaz (no network)"
	@echo "e2e        End-to-end: builds the image, runs init and Compose with a fake provider (needs Docker)"
	@echo "secrets    Scan the git history for secrets (gitleaks, redacted)"
	@echo "workflows  Security audit of the GitHub workflows (zizmor)"
	@echo "check      lint + typecheck + test + secrets + workflows (run before every commit)"
	@echo "audit      Known vulnerabilities in the locked dependencies (uv audit)"
	@echo "licenses   Licenses of the runtime dependencies"
	@echo "openapi    Regenerate docs/openapi.json from the app (a test checks it is up to date)"
	@echo "bench      Antifaz-Bench on MEDDOCAN (downloads 11.7 MB once; no LLM, no cost)"
	@echo "           make bench NER=1: also with the NER model (needs make ner-model)"
	@echo "           make bench DEV=1: only the NER threshold table on MEDDOCAN dev"
	@echo "           make bench PRESIDIO=1: also Presidio on the shared types (bench group, ~51 MB once)"
	@echo "ner-model  Install the ner extra and download the pinned NER model (about 1.16 GB)"
	@echo "demo       Regenerate docs/images/demo.gif from real commands (synthetic data, fake provider)"
	@echo "design-check  Panel prototype in Chromium: console, requests, overflow, motion, axe-core"
	@echo "           (Playwright + axe in the script's inline metadata; needs Playwright's Chromium)"

install:
	uv sync
	uv run pre-commit install

dev:
	uv run uvicorn --factory $(PKG).api.app:create_app --reload --no-access-log

# The image built from this clone (compose.build.yml) instead of the published one.
COMPOSE_LOCAL := docker compose -f docker-compose.yml -f compose.build.yml

up:
	$(COMPOSE_LOCAL) up -d --build

down:
	$(COMPOSE_LOCAL) down

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy src evals

test:
	uv run pytest --cov --cov-report=term-missing --cov-report=json
	uv run python -m scripts.check_coverage

# Official OpenAI and Anthropic SDKs against Antifaz and a fake provider. `make test` (and so
# `make check`) already runs them with the rest of tests/; this target is for a quick loop.
contract:
	uv run pytest tests/contract --no-cov

# The real image under Docker Compose with a fake provider (tests/e2e). Not part of `check`:
# it needs Docker and takes a few minutes. Never reads your .env (throwaway keys per run).
e2e:
	uv run pytest tests/e2e --no-cov --e2e -p no:cacheprovider

# Only the git history, never the working tree (gitleaks dir or --no-git would read .env),
# and always --redact so a finding never prints the secret.
secrets:
	gitleaks detect --source . --no-banner --redact

workflows:
	$(ZIZMOR) .

check: lint typecheck test secrets workflows

audit:
	$(UV_AUDIT)

licenses:
	uv run python -m scripts.check_licenses

# The gateway serves no /docs or /openapi.json (ADR-0015): the schema is a static file.
openapi:
	uv run python -m scripts.export_openapi

# NER=1: also the NER (threshold chosen on MEDDOCAN dev, then test once). DEV=1: only the dev
# threshold table (evals/results/<date>-<version>-ner-dev.json). PRESIDIO=1: also Presidio, from
# the `bench` dependency group (never shipped); it reads the NER results already saved, so it
# does not go with NER=1 (the group and the extra are never installed together).
bench:
	uv run $(if $(NER)$(DEV),--extra ner,) $(if $(PRESIDIO),--group bench,) python -m evals.run $(if $(NER),--ner,) $(if $(DEV),--dev-only,) $(if $(PRESIDIO),--presidio,)

# The optional NER: its extra (CPU-only torch) and the model pinned in detect/ner/manifest.json,
# checked by size and SHA-256. Note: a plain `uv sync` (make install) removes the extra again.
ner-model:
	uv sync --extra ner
	uv run python -m scripts.download_ner_model

# The README GIF (scripts/demo_gif.py): Antifaz and the fake provider of tests/e2e on ports 8000
# and 9000, throwaway keys, synthetic data, never your .env. Pillow comes for this run only (it is
# not a project dependency). ARGS=--text prints the captured session without drawing it.
demo:
	uv run --with pillow==12.3.0 python -m scripts.demo_gif $(ARGS)

# The panel prototype (design/prototype) in headless Chromium: console, third-party requests,
# horizontal scroll, clipped or ellipsised text, motion and axe-core, at 390/768/1024/1280 px, both
# themes and reduced motion. Not part of `check`. Its dependencies (Playwright, axe-playwright-python)
# are in the script's PEP 723 metadata, never in uv.lock; Chromium comes once with
# `uv run --with playwright==1.63.0 playwright install chromium`.
design-check:
	uv run scripts/check_design.py $(ARGS)
