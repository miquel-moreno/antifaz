# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added
- Library `mask()` and `restore()` (issue 4, part a): `[[TYPE_N]]` placeholders numbered by first appearance across texts, escaping of `[[` the user already wrote, single-pass restore of this request's placeholders only, default policy (mask everything but company CIFs), a `Vault` that cannot be printed, copied or pickled, and `DetectorFailed` when the detector breaks. Property tests for invariants 1, 5 and 6. ADR-0012 (proposed).
- Antifaz-Bench (issue 3, part 1): `make bench` measures the detector on the MEDDOCAN test set (downloaded and verified, never redistributed): per-type precision, recall, F1 (overlap and strict), leaks per 100 values including the types not covered yet, and latency. ADR-0011 (accepted) defines how hits and leaks are counted. First results in `docs/benchmark.md`.
- Detector (issue 2, part 2): email (Unicode local part), IPv4, Spanish phone (business and premium-rate numbers excluded; found next to other numbers), payment card (Luhn + known prefix, 7 brands), Spanish, Catalan and Galician style addresses, and with a keyword before them passport, plate, date of birth and the digit-only Portuguese, French and German identifiers.
- ADR-0010 (accepted): a span that contains another one wins, so an email with a DNI inside is masked whole.
- Detector (issue 2, part 1): check-digit validators for DNI, NIE, NIF K/L/M, CIF, NSS, CCC and IBAN, plus Italian codice fiscale and EU VAT via python-stdnum; `scan(text)` returns non-overlapping spans without the value (ADR-0009). Invariant 10: the DNI, NIE, NIF K/L/M, CIF, CCC and Spanish IBAN validators agree with python-stdnum on 5,000 generated cases; the NSS, which stdnum lacks, is checked against documented vectors.
- Project scaffold with one package per piece of the gateway (detect, policy, vault, mask, guard, providers, restore, audit, web, cli), `GET /healthz` and validation errors that never echo the request body.
- Architecture decision records 0001-0008 (accepted), threat model, `SECURITY.md`, contributing guide and code of conduct.
- CI: ruff, mypy --strict, tests with coverage (90 % required in the privacy pieces), gitleaks, `uv audit`, license check and CodeQL, with actions pinned by commit SHA. Dependabot for dependencies and actions.
- Claude Code kit in `.claude/` (agents, hooks and permissions), with tests for the hooks. The shell hook also watches PowerShell and blocks any mention of `.env` files, `.env` wildcards, `core.hooksPath`, `SKIP=` and force pushes with `git -C`.
