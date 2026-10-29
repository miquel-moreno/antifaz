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
- For the PostgreSQL tests (virtual keys, marked `postgres`): Docker, then `make test-postgres`.
  It starts the same `postgres:16` image as CI (pinned by digest in `.github/workflows/ci.yml`)
  with `docker run --rm` on 127.0.0.1 and a free port, with a random password, runs `make test`
  with `--postgres` and removes the container; `make test-postgres ARGS="tests/postgres -x"` runs
  only those tests. With your own PostgreSQL 16 instead, set `TEST_POSTGRES_HOST`,
  `TEST_POSTGRES_PORT`, `TEST_POSTGRES_USER`, `TEST_POSTGRES_PASSWORD` (a role that can create
  roles and databases) and `TEST_POSTGRES_DATABASE` (the database to connect to first), then
  `make test POSTGRES=1`. The tests only create and drop databases named `antifaz_test*` and
  their own role. A plain `make test` skips them (and the 90 % coverage rule of `db`); CI never
  does, and with `--postgres` and no database they fail instead of skipping.

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

## Adding a language to the panel

The panel's texts live in `src/antifaz/web/i18n/<code>.json`, one flat JSON object of
key -> plain text per language; `en.json` is the reference. To add a language you only add a
file, no code:

1. Copy `en.json` to `<code>.json`, where `<code>` is a language code (`fr`, `pt`, or with a
   region, `pt-BR`). Files named any other way are ignored.
2. Translate every text. Keep every key, keep each `{placeholder}` (for example `{seconds}`)
   exactly as it is, and write plain text: no HTML, no `<` or `>`, no empty texts.
3. Set `language.name` to the language's own name (`Français`, `Português`): it labels the link
   in the language selector.
4. Run `make check`. The catalog tests run over every file they find, so the new one is checked
   for the same keys and placeholders as English; a broken catalog also stops the gateway from
   starting, with one line naming the file and the key.

The new language then appears in the selector, and browsers that ask for it (`Accept-Language`)
get it. Use `make panel-captures` to look at the pages.

By contributing you agree that your contribution is licensed under Apache-2.0.
