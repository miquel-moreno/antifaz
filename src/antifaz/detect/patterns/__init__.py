"""Patterns with context for data without a check digit (phone, email, passport, plate...).

Written so they cannot backtrack catastrophically, and fuzzed (ADR-0008).

Forbidden: No customer-provided regular expressions here: those go through re2 in the policy.
"""
