"""Placeholder table: value <-> placeholder for the current request only (ADR-0004).

Numbered by first appearance over the whole conversation, so the same value gets the
same placeholder without keeping state between requests.

Forbidden: Never written to logs, traces, errors or metrics. Never persisted in clear text.
"""
