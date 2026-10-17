# Contributing

Thanks for helping. Antifaz handles personal data, so a few rules are stricter than usual.

## Rules

- **Invented data only.** Never paste real names, IDs, IBANs or messages into issues,
  tests or examples. Generate valid-looking identifiers with their algorithm.
- **Security problems go through `SECURITY.md`**, not public issues.
- **A value Antifaz misses** goes in a public issue with the "I found a leak" template,
  with invented data only.
- **Every change keeps the privacy invariants green** (see `docs/TECNICO.md`): nothing the
  policy hides reaches a provider, and anything that fails while checking a request blocks it.
- **Tests first.** New detectors come with tests that show what they find and what they
  must not find.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/) for everything (`make install`, `make check`).
- **Docker Engine 28 or later with Compose v2 (`docker compose`) for `make e2e`.** The network
  isolation of the end-to-end tests depends on it (see `docs/TECNICO.md`).
- For `make panel-check` (the panel in real browsers, not part of `make check`): the `panel`
  dependency group and Playwright's browsers, installed once outside the repo with
  `uv run --group panel playwright install chromium firefox webkit` (about 120–205 MB each).
  `make panel-check PANEL_BROWSERS=chromium` runs one browser only.

## Workflow

1. Open or pick an issue.
2. Create a branch and make your change.
3. Run `make install` once, then `make check` (lint, types, tests, secret scan, workflow audit) until it passes.
   The `forbidden-names` pre-commit hook reads a private list of names that must never appear
   (one per line) from `../_privado/forbidden-names.txt` next to your clone, or from the path in
   `ANTIFAZ_FORBIDDEN_NAMES_FILE`. Without that file it stops the commit and says where to put it;
   only in CI (`CI=true`) it warns and passes.
4. Open a pull request with a short description. Use Conventional Commits
   (`feat:`, `fix:`, `docs:`, `test:`...).

By contributing you agree that your contribution is licensed under Apache-2.0.
