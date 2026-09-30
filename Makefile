# Common tasks. Run `make help` to list them.
PKG := antifaz
UV_AUDIT := uvx --from uv==0.12.20 uv audit --frozen --preview-features audit-command
ZIZMOR := uvx zizmor==1.30.1 --offline --persona auditor --format plain

.PHONY: help install dev lint format typecheck test contract secrets check audit licenses bench workflows

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

bench:
	uv run python -m evals.run
