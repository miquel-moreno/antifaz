"""Evidence log (v0.2): one hash-chained row per request, without personal data.

Single writer and signed checkpoints outside the database.

Forbidden: Never store values, or unkeyed hashes of low-entropy data (use HMAC with a server key).
"""
