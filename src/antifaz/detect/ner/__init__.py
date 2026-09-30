"""Named-entity recognition for names and addresses (ADR-0003, ADR-0016).

Pieces: `chunker` (overlapping windows, GLiNER cuts long texts without warning), `cache` (keyed
BLAKE2b, offsets and types only), `manifest` (SHA-256 of every model file, checked before
loading), `pool` and `worker` (separate processes killed on timeout), `engine` (the detector
layer in front of the pool) and `setup` (startup from the settings). The real model backend is
the optional extra `antifaz[ner]` (issue 6b); `fake` is a dictionary backend for tests.

Forbidden: No generative AI and no network at runtime. Never run a model in the gateway
process. Never send exception text or values from a worker.
"""
