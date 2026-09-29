"""Egress guard: a second check on the final bytes before they leave for a provider.

Looks for any value the policy said to hide, in the raw bytes and in decoded JSON
strings, with normalisation and word boundaries. If it finds one, the request is
blocked.

Forbidden: It cannot be disabled.
"""
