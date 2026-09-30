"""Cache of NER spans in the gateway process, in front of the pool (ADR-0016).

Chat clients resend the whole history on every turn: without a cache, the same text would go
through the model again and again. The key is a BLAKE2b hash KEYED with a random secret made
at import time (a new one on every start): without the secret, a memory dump cannot be used to
test whether a given DNI or name went through here. The key covers the text, the model manifest,
the labels, the threshold and the chunker version, so changing any of them misses the cache.
Only spans (offsets and types) are stored, never the text or a value. Bounded LRU.
"""

import hashlib
import secrets
import threading
from collections import OrderedDict
from collections.abc import Iterable

from antifaz.detect.types import Span

_SECRET = secrets.token_bytes(32)
DEFAULT_ENTRIES = 10_000


def _field(data: bytes) -> bytes:
    return len(data).to_bytes(8, "big") + data  # length-prefixed: fields cannot shift


def cache_key(
    text: str, *, model: str, labels: Iterable[str], threshold: float, chunker: str
) -> bytes:
    digest = hashlib.blake2b(key=_SECRET, digest_size=32)
    for part in (text, model, "\x1f".join(labels), repr(float(threshold)), chunker):
        digest.update(_field(part.encode("utf-8", "surrogatepass")))
    return digest.digest()


class SpanCache:
    """Thread-safe LRU of key -> spans, with at most `max_entries` entries (0 disables it)."""

    def __init__(self, max_entries: int = DEFAULT_ENTRIES) -> None:
        if max_entries < 0:
            raise ValueError("max_entries cannot be negative")
        self._max = max_entries
        self._entries: OrderedDict[bytes, tuple[Span, ...]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: bytes) -> tuple[Span, ...] | None:
        with self._lock:
            spans = self._entries.get(key)
            if spans is not None:
                self._entries.move_to_end(key)
            return spans

    def put(self, key: bytes, spans: Iterable[Span]) -> None:
        if not self._max:
            return
        with self._lock:
            self._entries[key] = tuple(spans)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        return f"SpanCache(entries={len(self)}, max_entries={self._max})"
