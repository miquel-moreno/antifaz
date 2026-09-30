"""The NER span cache (ADR-0016): keyed BLAKE2b, offsets and types only, bounded LRU."""

import threading

from antifaz.detect.ner.cache import SpanCache, cache_key
from antifaz.detect.types import Confidence, EntityType, Layer, Span
from tests.conftest import SENTINEL_DNI

SPAN = Span(0, 6, EntityType.PERSON, Layer.NER, Confidence.MEDIUM)
PARAMS = {"model": "m1", "labels": ("person",), "threshold": 0.5, "chunker": "1"}


def _key(text: str, **changes: object) -> bytes:
    return cache_key(text, **{**PARAMS, **changes})  # type: ignore[arg-type]


def test_same_text_and_settings_give_the_same_key() -> None:
    assert _key("Carmen") == _key("Carmen")


def test_every_setting_changes_the_key() -> None:
    base = _key("Carmen")
    assert _key("Carmen ") != base
    assert _key("Carmen", model="m2") != base
    assert _key("Carmen", labels=("person", "address")) != base
    assert _key("Carmen", threshold=0.6) != base
    assert _key("Carmen", chunker="2") != base


def test_fields_cannot_be_shifted_into_each_other() -> None:
    assert cache_key("ab", model="c", labels=(), threshold=0.5, chunker="1") != cache_key(
        "a", model="bc", labels=(), threshold=0.5, chunker="1"
    )


def test_the_key_is_not_a_plain_hash_of_the_text() -> None:
    import hashlib

    key = _key(SENTINEL_DNI)
    assert key != hashlib.blake2b(SENTINEL_DNI.encode()).digest()
    assert key != hashlib.sha256(SENTINEL_DNI.encode()).digest()
    assert SENTINEL_DNI.encode() not in key
    assert len(key) == 32


def test_a_miss_then_a_hit() -> None:
    cache = SpanCache(10)
    assert cache.get(b"k") is None
    cache.put(b"k", [SPAN])
    assert cache.get(b"k") == (SPAN,)


def test_it_keeps_at_most_the_configured_entries_dropping_the_least_used() -> None:
    cache = SpanCache(2)
    cache.put(b"a", [SPAN])
    cache.put(b"b", [])
    assert cache.get(b"a") == (SPAN,)  # "a" is now the most recently used
    cache.put(b"c", [])
    assert len(cache) == 2
    assert cache.get(b"b") is None
    assert cache.get(b"a") == (SPAN,)


def test_zero_entries_disables_it() -> None:
    cache = SpanCache(0)
    cache.put(b"a", [SPAN])
    assert cache.get(b"a") is None
    assert len(cache) == 0


def test_it_stores_spans_only_never_text() -> None:
    cache = SpanCache(4)
    cache.put(_key(SENTINEL_DNI), [SPAN])
    assert SENTINEL_DNI not in repr(cache)
    assert repr(cache) == "SpanCache(entries=1, max_entries=4)"


def test_it_is_safe_to_use_from_several_threads() -> None:
    cache = SpanCache(50)

    def work(n: int) -> None:
        for i in range(200):
            cache.put(f"{n}-{i}".encode(), [SPAN])
            cache.get(f"{n}-{i - 1}".encode())

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(cache) == 50


def test_it_is_also_bounded_by_the_total_number_of_spans() -> None:
    cache = SpanCache(100, max_spans=3)
    cache.put(b"a", [SPAN, SPAN])
    cache.put(b"b", [SPAN])
    assert cache.spans == 3
    cache.put(b"c", [SPAN])  # 4 spans: the least recently used entry goes
    assert cache.get(b"a") is None
    assert cache.spans == 2
    cache.put(b"huge", [SPAN] * 4)  # more than the whole bound: never stored
    assert cache.get(b"huge") is None
    assert cache.spans == 2


def test_replacing_an_entry_keeps_the_span_count_right() -> None:
    cache = SpanCache(10, max_spans=10)
    cache.put(b"a", [SPAN, SPAN])
    cache.put(b"a", [SPAN])
    assert cache.spans == 1


def test_a_negative_span_bound_is_refused() -> None:
    import pytest

    with pytest.raises(ValueError):
        SpanCache(10, max_spans=-1)
