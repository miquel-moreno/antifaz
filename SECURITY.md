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
service). This includes reaching a route without the key, using the gateway from a web
page in someone's browser, getting a request past the gateway with a `Host` it does not
allow, or making it start without a safe key.

## Out of scope

- **Data that the detector does not find.** No detector is perfect; the benchmark
  (`docs/benchmark.md`) says how much slips through. Better detection is very welcome as a
  normal issue or pull request.
- **What the provider infers** from the text left unmasked (context, writing style).
- **Someone who controls the server or its configuration**: they can read `.env`, change
  the policy or turn settings off. Antifaz does not protect the operator from themselves.
- **Settings the operator chose on purpose**: an origin added to
  `ANTIFAZ_ALLOWED_ORIGINS`, a host added to `ANTIFAZ_ALLOWED_HOSTS`, or a policy that
  allows a data type. (A default that is unsafe *is* in scope.)
- **Deployments without TLS** or behind a proxy that rewrites or adds headers (CORS, `Host`):
  see `docs/TECNICO.md` for how to deploy it.
- **The provider's own error messages** that show part of its own key (for example
  `sk-...abcd`); the full key is never returned.
- **Denial of service by volume** (many requests from many clients): v0.1 has size and time
  limits, but no rate limits yet.
