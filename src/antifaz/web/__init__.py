"""Playground and admin panel (v0.2, ADR-0018): Jinja2 + native JavaScript, no HTMX.

Try a text, see live counts, keys, policies and status, under /panel only. Without
ANTIFAZ_ADMIN_TOKEN the panel does not exist and nothing here is loaded.

Forbidden: Never store what is tried in the playground: it lives only in the request. Never
read the admin token itself: the panel only gets its fingerprint.
"""
