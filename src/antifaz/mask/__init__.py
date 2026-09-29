"""Masker: replaces detected values with placeholders in every known text field.

Escapes any `[[...]]` the user already wrote (ADR-0002).

Forbidden: Never touches non-text fields or reasoning (thinking) blocks returned by the model.
"""
