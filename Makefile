# Common tasks. Run `make help` to list them.
PKG := antifaz
UV_AUDIT := uvx --from uv==0.13.0 uv audit --frozen --preview-features audit-command
ZIZMOR := uvx zizmor==1.30.1 --offline --persona auditor --format plain

.PHONY: help install dev up down lint format typecheck test contract e2e secrets check audit licenses bench workflows ner-model openapi demo design-check design-captures panel-check panel-captures

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
	@echo "           (Playwright, axe and Pillow in the script's inline metadata; needs Playwright's Chromium)"
	@echo "design-captures  Reference captures of the prototype into design/captures/ (same script)"
	@echo "panel-check  The real panel in Chromium, Firefox and WebKit: CSP, console, cookies, axe"
	@echo "           (panel group; PANEL_BROWSERS=chromium for one; browsers installed once, see CONTRIBUTING)"
	@echo "panel-captures  Captures of the real panel (es, en; light, dark; 1280, 390 and 320 px) into build/panel-captures/"

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
# -vv: the CI log names every test that ran (pyproject's addopts has -q, which cancels one -v).
e2e:
	uv run pytest tests/e2e -vv --no-cov --e2e -p no:cacheprovider

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
# horizontal scroll, clipped or ellipsised text, identifiers broken inside, motion and axe-core, at
# 390/768/1024/1280 px, both themes and reduced motion. Not part of `check`. Its dependencies
# (Playwright, axe-playwright-python, Pillow) are in the script's PEP 723 metadata (Playwright and axe
# with the same pins as the `panel` group of uv.lock; a test keeps them equal); Chromium comes once with
# `uv run --with playwright==1.63.0 playwright install chromium`.
design-check:
	uv run scripts/check_design.py $(ARGS)

# The real panel (tests/browser) in real browsers with Playwright: an in-process gateway on
# 127.0.0.1, throwaway tokens, no network, no provider. Not part of `check`. Its dependencies are
# the `panel` group (never installed by `uv sync`); the browsers come once with
# `uv run --group panel playwright install chromium firefox webkit`.
# PANEL_BROWSERS=chromium (or a comma list) limits the run; CI runs Chromium only.
panel-check:
	uv run --frozen --group panel pytest tests/browser -vv --no-cov --panel -p no:cacheprovider $(if $(PANEL_BROWSERS),--panel-browser $(PANEL_BROWSERS),)

# Captures of the real panel (tests/browser/test_panel_captures.py) into build/panel-captures/,
# which git ignores: login empty, login with an error and the panel, in Spanish and English, light
# and dark, at 1280x800 and 390x844 (2x), plus the phone settings menu open and the phone panel
# with the text at 200 % (390x844 and 320x844), and the Status view with its data and in its
# error state (11c-3b), the login and Status under forced colours (11c-4), and «Prueba un texto»
# empty, with a result, with the too-long offer, without the name detector and with the session
# warning, at 1280 and 390 px (11d-b), «En directo» (11e-2), and «Comprobar ahora» (ok, mixed,
# cooldown) and the header pill in warn and danger (11e-4), with the in-process fake provider
# only. Synthetic only: the
# token field is checked empty before every capture. Chromium only. Not part of `check` nor of `panel-check`.
panel-captures:
	uv run --frozen --group panel pytest tests/browser/test_panel_captures.py -vv --no-cov --panel --panel-captures --panel-browser chromium -p no:cacheprovider

# Reference captures of the prototype (design/captures/): the five views in light and dark, desktop
# and mobile, plus the other states of the Playground. Same script and dependencies as design-check.
design-captures:
	uv run scripts/check_design.py --captures
