"""Gate: the HTTP API (OpenAI- and Anthropic-compatible endpoints from issue 5).

Authentication, size and rate limits, and a request id on every request. Its own
validation-error handler, so no error ever echoes the received body.

Forbidden: logging request or response bodies. Forwarding the client's key.
"""
