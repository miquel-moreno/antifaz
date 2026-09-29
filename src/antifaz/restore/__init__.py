"""Restorer: puts the original values back in the answer, plain or streaming.

Only placeholders emitted in this same request are restored; unknown ones are left as
they are. Inside JSON (tool arguments) the value is escaped as a JSON string.

Forbidden: Never restore inside reasoning blocks or unknown events: those pass through untouched.
"""
