# Security policy

Antifaz exists to keep personal data away from LLM providers, so security reports are
the most valuable contribution you can make.

## Reporting a vulnerability

**Please do not open a public issue.** Report it privately through GitHub:
**Security → Report a vulnerability** on this repository (private vulnerability reporting).

Include, if you can:

- what an attacker can do (for example: personal data reaches the provider, data from one
  user is restored for another, a request that should be blocked goes through);
- the steps or a minimal request to reproduce it, **with invented data only**;
- the version or commit you tested.

You will get an answer within 7 days. Once a fix is released, the advisory is published
and you are credited, unless you prefer not to be.

## Supported versions

Antifaz is in early development (0.x). Only the latest release receives security fixes.

## Scope

In scope: anything that lets detected personal data leave towards a provider, restores
data across requests or users, bypasses the fail-closed checks, leaks data into logs,
errors or metrics, or compromises the gateway itself (SSRF, key exposure, denial of
service).

Out of scope: data that the detector does not find. No detector is perfect; the
benchmark (`docs/benchmark.md`, from v0.1) says how much slips through. Better detection is very
welcome as a normal issue or pull request.
