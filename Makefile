# Common tasks. Run `make help` to list them.
PKG := antifaz
UV_AUDIT := uvx --from uv==0.12.20 uv audit --frozen --preview-features audit-command

.PHONY: help install dev lint format typecheck test secrets check audit licenses

help:
	@echo "install    Install dependencies and git hooks"
	@echo "dev        Run the API with auto-reload on http://localhost:8000"
	@echo "lint       Check style and common bugs (ruff)"
	@echo "format     Auto-format the code (ruff)"
	@echo "typecheck  Check types (mypy strict)"
	@echo "test       Tests with coverage (80 % overall, 90 % in the privacy pieces)"
	@echo "secrets    Scan the repository for secrets (gitleaks)"
	@echo "check      lint + typecheck + test + secrets (run before every commit)"
	@echo "audit      Known vulnerabilities in the locked dependencies (uv audit)"
	@echo "licenses   Licenses of the runtime dependencies"

install:
	uv sync
	uv run pre-commit install

dev:
	uv run uvicorn $(PKG).api.app:app --reload

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy src

test:
	uv run pytest --cov --cov-report=term-missing --cov-report=json
	uv run python -m scripts.check_coverage

secrets:
	gitleaks detect --source . --no-banner

check: lint typecheck test secrets

audit:
	$(UV_AUDIT)

licenses:
	uv run python -m scripts.check_licenses
