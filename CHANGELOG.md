# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added
- Project scaffold with one package per piece of the gateway (detect, policy, vault, mask, guard, providers, restore, audit, web, cli), `GET /healthz` and validation errors that never echo the request body.
- Architecture decision records 0001-0008 (proposed), threat model, `SECURITY.md`, contributing guide and code of conduct.
- CI: ruff, mypy --strict, tests with coverage (90 % required in the privacy pieces), gitleaks, `uv audit`, license check and CodeQL, with actions pinned by commit SHA. Dependabot for dependencies and actions.
- Claude Code kit in `.claude/` (agents, hooks and permissions), with tests for the hooks.
