"""Overlapping windows for the NER (ADR-0016): GLiNER cuts long texts without warning."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from antifaz.detect.ner.chunker import (
    CHUNKER_VERSION,
    MAX_TOKEN_CHARS,
    OVERLAP_TOKENS,
    WINDOW_TOKENS,
    Chunk,
    chunk,
    merge,
    token_count,
)


def test_defaults_stay_well_below_the_gliner_limit_of_384_words() -> None:
    assert WINDOW_TOKENS <= 250
    assert 0 < OVERLAP_TOKENS < WINDOW_TOKENS
    assert CHUNKER_VERSION  # part of the cache key


def test_empty_or_blank_text_has_no_chunks() -> None:
    assert chunk("") == []
    assert chunk(" \n\t ") == []


def test_a_short_text_is_one_chunk_without_the_outer_spaces() -> None:
    assert chunk("  Hola, Carmen.  ") == [Chunk(2, "Hola, Carmen.")]


def test_every_sign_counts_as_one_token_and_long_words_are_split() -> None:
    assert token_count("Hola, ¿qué tal?") == 6
    assert token_count("a" * (MAX_TOKEN_CHARS * 2 + 1)) == 3


def test_windows_overlap_and_map_back_to_the_text() -> None:
    text = " ".join(f"w{i}" for i in range(10))
    chunks = chunk(text, window=4, overlap=2)
    assert [c.text for c in chunks] == [
        "w0 w1 w2 w3",
        "w2 w3 w4 w5",
        "w4 w5 w6 w7",
        "w6 w7 w8 w9",
    ]
    for piece in chunks:
        assert text[piece.start : piece.start + len(piece.text)] == piece.text


@pytest.mark.parametrize(("window", "overlap"), [(0, 0), (4, 4), (4, -1), (4, 5)])
def test_invalid_window_settings_are_refused(window: int, overlap: int) -> None:
    with pytest.raises(ValueError, match="window"):
        chunk("uno dos", window=window, overlap=overlap)


def test_merge_shifts_offsets_and_joins_overlapping_entities_of_the_same_label() -> None:
    chunks = [Chunk(0, "a b c d"), Chunk(4, "c d e f")]
    results = [
        [(4, 7, "person", 0.6)],  # "c d" seen at the end of the first window
        [(0, 5, "person", 0.9), (6, 7, "address", 0.8)],  # "c d e" and "f" in the second
    ]
    assert merge(chunks, results) == [(4, 9, "person", 0.9), (10, 11, "address", 0.8)]


def test_merge_keeps_touching_entities_apart() -> None:
    assert merge([Chunk(0, "ab")], [[(0, 1, "person", 0.5), (1, 2, "person", 0.7)]]) == [
        (0, 1, "person", 0.5),
        (1, 2, "person", 0.7),
    ]


words = st.lists(st.sampled_from(["a", "bb", ",", "ñu", "Carmen", "12"]), max_size=60)


@settings(max_examples=300, deadline=None)
@given(parts=words, spaces=st.sampled_from([" ", "  ", "\n"]))
def test_every_token_is_in_a_chunk_that_maps_back_exactly(parts: list[str], spaces: str) -> None:
    text = spaces.join(parts)
    chunks = chunk(text, window=5, overlap=2)
    covered = set()
    for piece in chunks:
        assert text[piece.start : piece.start + len(piece.text)] == piece.text
        assert token_count(piece.text) <= 5
        covered.update(range(piece.start, piece.start + len(piece.text)))
    assert {i for i, char in enumerate(text) if not char.isspace()} <= covered
