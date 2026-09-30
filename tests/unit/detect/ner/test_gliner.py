"""The GLiNER backend without torch: config rewrite, pieces that fit the model, offsets back.

The loaded model is replaced by three small functions (split words, count model tokens, infer),
so these tests run in CI without the `ner` extra. The real model is tested in
tests/integration/test_ner_model.py (marker `ner_model`).
"""

import json
import re
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from antifaz.detect.ner import gliner as backend
from antifaz.detect.ner.backend import load_factory
from antifaz.detect.ner.gliner import GlinerBackend, WordTooLongError, model_config, pieces
from antifaz.detect.ner.worker import OFFLINE_ENV
from tests.nerfakes import CARMEN, JORDI

_WORD = re.compile(r"\w+(?:[-_]\w+)*|\S")  # GLiNER's own word splitter


def split_words(text: str) -> Iterator[tuple[str, int, int]]:
    for match in _WORD.finditer(text):
        yield match.group(), match.start(), match.end()


def count_tokens(words: list[str], labels: list[str]) -> tuple[int, list[int]]:
    """A stand-in tokenizer: one token per 3 characters of a word, 2 per label + 3 fixed."""
    return 3 + 2 * len(labels), [len(word) // 3 + 1 for word in words]


class Recorder:
    """A stand-in model: finds the given names by text search and records what it read."""

    def __init__(self, names: dict[str, str]) -> None:
        self.names = names
        self.read: list[str] = []

    def __call__(
        self, texts: list[str], labels: list[str], threshold: float
    ) -> list[list[dict[str, Any]]]:
        self.read.extend(texts)
        out = []
        for text in texts:
            found = []
            for value, label in self.names.items():
                start = text.find(value)
                if start != -1 and label in labels:
                    found.append({"start": start, "end": start + len(value), "label": label,
                                  "score": 0.875, "text": value})  # fmt: skip
            out.append(found)
        return out


def test_the_config_points_the_encoder_to_the_local_tokenizer(tmp_path: Path) -> None:
    original = {"model_name": "microsoft/mdeberta-v3-base", "max_len": 384, "max_width": 12}
    (tmp_path / "gliner_config.json").write_text(json.dumps(original), encoding="utf-8")

    config = model_config(tmp_path)

    assert config["model_name"] == str(tmp_path / "mdeberta-v3-base")
    rest = {key: value for key, value in config.items() if key != "model_name"}
    assert rest == {"max_len": 384, "max_width": 12}
    # The file itself is never changed (its SHA-256 is in the manifest).
    assert json.loads((tmp_path / "gliner_config.json").read_text(encoding="utf-8")) == original


def test_the_factory_is_importable_without_the_extra() -> None:
    """The pool imports `create` by path; torch and gliner are imported only when it runs."""
    assert load_factory("antifaz.detect.ner.gliner:create") is backend.create


def test_the_backend_sets_the_same_offline_variables_as_the_worker() -> None:
    assert backend.OFFLINE_ENV == OFFLINE_ENV
    assert OFFLINE_ENV["HF_HUB_OFFLINE"] == OFFLINE_ENV["TRANSFORMERS_OFFLINE"] == "1"


@given(
    st.lists(st.integers(min_value=1, max_value=40), max_size=300),
    st.integers(min_value=40, max_value=400),
    st.integers(min_value=0, max_value=60),
)
def test_pieces_cover_every_word_within_the_budget(
    counts: list[int], budget: int, overlap: int
) -> None:
    ranges = pieces(counts, budget, overlap)

    covered = sorted({index for words in ranges for index in words})
    assert covered == list(range(len(counts)))
    for words in ranges:
        assert len(words) >= 1
        assert sum(counts[index] for index in words) <= budget
    for before, after in pairwise(ranges):
        assert before.start < after.start <= before.stop  # forward, no gap
        assert before.stop - after.start <= overlap  # shared words
        assert sum(counts[i] for i in range(after.start, before.stop)) <= budget // 2


def test_pieces_share_words_so_a_name_on_the_border_is_whole_once() -> None:
    ranges = pieces([1] * 100, budget=40, overlap=10)
    assert ranges[0] == range(0, 40)
    assert ranges[1].start == 30  # 10 words shared


@pytest.mark.parametrize(("counts", "budget"), [([1, 50, 1], 49), ([1], 0)])
def test_a_word_that_cannot_fit_is_refused(counts: list[int], budget: int) -> None:
    with pytest.raises(WordTooLongError):
        pieces(counts, budget)


def test_a_short_text_is_one_piece_read_as_is() -> None:
    model = Recorder({JORDI: "person"})
    gliner = GlinerBackend(split_words, count_tokens, model)
    text = f"  El paciente {JORDI} ingresó.  "

    (entities,) = gliner.predict([text], ["person", "address"], 0.5)

    start = text.index(JORDI)
    assert entities == [[start, start + len(JORDI), "person", 0.875]]
    assert model.read == [text.strip()]


def test_blank_texts_are_not_sent_to_the_model() -> None:
    model = Recorder({})
    gliner = GlinerBackend(split_words, count_tokens, model)

    assert gliner.predict(["", "   \n"], ["person"], 0.5) == [[], []]
    assert model.read == []


def test_a_long_window_is_cut_to_fit_and_offsets_come_back_to_the_text() -> None:
    """Long numbers make many model tokens: the window is read in pieces, and a name in the
    last piece is found at its place in the whole text."""
    filler = " ".join("1234567890" * 4 for _ in range(60))
    text = f"{CARMEN} tiene {filler} y {JORDI} al final."
    model = Recorder({CARMEN: "person", JORDI: "person"})
    gliner = GlinerBackend(split_words, count_tokens, model, max_tokens=120, overlap=8)

    (entities,) = gliner.predict([text], ["person", "address"], 0.5)

    assert len(model.read) > 1
    fixed, _ = count_tokens([], ["person", "address"])
    for piece in model.read:
        words = [word for word, _, _ in split_words(piece)]
        assert fixed + sum(count_tokens(words, [])[1]) <= 120
    assert [(e[0], e[1]) for e in entities] == [
        (text.index(CARMEN), text.index(CARMEN) + len(CARMEN)),
        (text.index(JORDI), text.index(JORDI) + len(JORDI)),
    ]


def test_a_name_seen_in_two_pieces_is_one_entity() -> None:
    words = " ".join(f"w{i}" for i in range(30))
    text = f"{words} {JORDI} {words}"
    model = Recorder({JORDI: "person"})
    # Every word costs 1-3 tokens: pieces of 30 tokens sharing 8 words.
    gliner = GlinerBackend(split_words, count_tokens, model, max_tokens=37, overlap=8)

    (entities,) = gliner.predict([text], ["person", "address"], 0.5)

    assert sum(JORDI in piece for piece in model.read) >= 1
    start = text.index(JORDI)
    assert entities == [[start, start + len(JORDI), "person", 0.875]]


def test_scores_and_offsets_become_plain_json_types() -> None:
    class Numpyish(float):
        pass

    def infer(texts: list[str], labels: list[str], threshold: float) -> list[list[dict[str, Any]]]:
        return [[{"start": 0, "end": 2, "label": "person", "score": Numpyish(0.5)}]]

    (entities,) = GlinerBackend(split_words, count_tokens, infer).predict(["Al"], ["person"], 0.5)

    assert type(entities[0][3]) is float
    json.dumps(entities)


def test_a_tokenizer_that_skips_words_is_an_error() -> None:
    def short(words: list[str], labels: list[str]) -> tuple[int, list[int]]:
        return 0, [1] * (len(words) - 1)

    with pytest.raises(ValueError, match="every word"):
        GlinerBackend(split_words, short, Recorder({})).predict(["dos palabras"], ["person"], 0.5)


def test_overlapping_entities_of_two_pieces_are_left_for_the_engine_to_join() -> None:
    """The backend only drops exact repeats (keeping the best score) and never joins
    overlapping spans: so its answer at a threshold is its answer at a lower one filtered by
    score, which lets the bench run the model once for every threshold."""

    def infer(texts: list[str], labels: list[str], threshold: float) -> list[list[dict[str, Any]]]:
        return [
            [
                {"start": 0, "end": 5, "label": "person", "score": 0.4},
                {"start": 0, "end": 5, "label": "person", "score": 0.9},
                {"start": 3, "end": 9, "label": "person", "score": 0.6},
            ]
            for _ in texts
        ]

    (entities,) = GlinerBackend(split_words, count_tokens, infer).predict(
        ["Ana Bel Prueba"], ["person"], 0.3
    )

    assert entities == [[0, 5, "person", 0.9], [3, 9, "person", 0.6]]
