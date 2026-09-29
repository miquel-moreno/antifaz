"""Detector: finds personal data in text and returns spans.

Layers: check-digit validators, patterns with context, NER, customer dictionaries and
special-category signals. Spans are cached by content hash, because chat clients
resend the whole history on every turn.

Forbidden: No network. No generative AI. No regular expressions that can backtrack (ADR-0008).
"""
