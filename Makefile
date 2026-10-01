# Common tasks. Run `make help` to list them.
PKG := antifaz
UV_AUDIT := uvx --from uv==0.12.20 uv audit --frozen --preview-features audit-command
ZIZMOR := uvx zizmor==1.30.1 --offline --persona auditor --format plain

.PHONY: help install dev lint format typecheck test contract secrets check audit licenses bench workflows ner-model

help:
	@echo "install    Install dependencies and git hooks"
	@echo "dev        Run the API with auto-reload on http://localhost:8000"
	@echo "lint       Check style and common bugs (ruff)"
	@echo "format     Auto-format the code (ruff)"
	@echo "typecheck  Check types (mypy strict)"
	@echo "test       Tests with coverage (80 % overall, 90 % in the privacy pieces)"
	@echo "contract   Only the contract tests: official SDKs against Antifaz (no network)"
	@echo "secrets    Scan the repository for secrets (gitleaks)"
	@echo "workflows  Security audit of the GitHub workflows (zizmor)"
	@echo "check      lint + typecheck + test + secrets + workflows (run before every commit)"
	@echo "audit      Known vulnerabilities in the locked dependencies (uv audit)"
	@echo "licenses   Licenses of the runtime dependencies"
	@echo "bench      Antifaz-Bench on MEDDOCAN (downloads 11.7 MB once; no LLM, no cost)"
	@echo "           make bench NER=1: also with the NER model (needs make ner-model)"
	@echo "           make bench DEV=1: only the NER threshold table on MEDDOCAN dev"
	@echo "           make bench PRESIDIO=1: also Presidio on the shared types (bench group, ~51 MB once)"
	@echo "ner-model  Install the ner extra and download the pinned NER model (about 1.16 GB)"

install:
	uv sync
	uv run pre-commit install

dev:
	uv run uvicorn --factory $(PKG).api.app:create_app --reload --no-access-log

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

secrets:
	gitleaks detect --source . --no-banner

workflows:
	$(ZIZMOR) .

check: lint typecheck test secrets workflows

audit:
	$(UV_AUDIT)

licenses:
	uv run python -m scripts.check_licenses

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
