"""The NER input view: blobs and long digit runs glued to letters become spaces.

Offsets never move (same length), so NER spans map back unchanged; validators and patterns
still read the full text.
"""

import base64
import hashlib
import random

from hypothesis import given
from hypothesis import strategies as st

from antifaz.detect.ner.view import ner_view

NAME = "Jordi Inventat Puig"


def _blank(text: str, part: str) -> str:
    return text.replace(part, " " * len(part))


def test_plain_spanish_is_untouched() -> None:
    text = f"Hola, soy {NAME} y vivo en la Calle Ficticia 12, 08001 Barcelona."
    assert ner_view(text) == text


def test_a_base64_blob_is_hidden_from_the_model() -> None:
    rng = random.Random(4)  # noqa: S311 - fixed seed for test data, not security
    blob = base64.b64encode(rng.randbytes(3000)).decode()
    text = f"Adjunto: {blob} Firmado: {NAME}"

    assert ner_view(text) == _blank(text, blob)


def test_hashes_and_long_urls_are_hidden() -> None:
    digest = hashlib.sha256(b"x").hexdigest()
    url = "https://example.com/descargas/informe?id=0123456789abcdef&token=abc"
    text = f"sha {digest} en {url} para {NAME}"

    assert ner_view(text) == _blank(_blank(text, digest), url)


def test_a_short_url_and_csv_rows_without_spaces_stay() -> None:
    text = f"web http://a.es y Jordi,Inventat,Puig,Calle,Ficticia,12 de {NAME}"
    assert ner_view(text) == text


def test_a_long_word_of_letters_only_stays_unless_it_is_huge() -> None:
    word = "Esternocleidomastoideoquirurgico"  # 32 letters, no digits or signs
    assert ner_view(word) == word
    assert ner_view("a" * 64) == " " * 64


def test_a_name_glued_to_a_long_number_is_split_for_the_model() -> None:
    text = f"Ref 1234567890{NAME} pidió cita"
    assert ner_view(text) == f"Ref           {NAME} pidió cita"
    assert ner_view(f"{NAME}9876543210 ok") == f"{NAME}           ok"


def test_short_numbers_next_to_letters_stay() -> None:
    text = "Piso 3ºB, portal 12A, CP 08001, tel 600111222"
    assert ner_view(text) == text


@given(st.text(max_size=300))
def test_the_view_keeps_the_length_and_only_blanks_characters(text: str) -> None:
    view = ner_view(text)
    assert len(view) == len(text)
    assert all(v == t or v == " " for v, t in zip(view, text, strict=True))


def test_long_runs_take_linear_time() -> None:
    """ADR-0008: no pattern here retries a long run from every position."""
    import time

    for text in ("1" * 200_000, "a" * 200_000 + "://", "ab" * 100_000, "1" * 200_000 + " x"):
        started = time.perf_counter()
        ner_view(text)
        assert time.perf_counter() - started < 1.0


def test_the_engine_sends_the_view_and_spans_keep_their_place() -> None:
    from tests.nerfakes import JORDI, fake_detector

    blob = "QUJD" * 40
    text = f"Adjunto {blob} y ref 1234567890{JORDI} al final."
    detector, predictor = fake_detector()

    (spans,) = detector.find_many([text])

    assert all(blob not in sent and "1234567890" not in sent for sent in predictor.texts)
    start = text.index(JORDI)
    assert [(span.start, span.end) for span in spans] == [(start, start + len(JORDI))]


def test_long_numbers_are_hidden_from_the_model() -> None:
    number = "1234567890" * 4
    assert ner_view(f"Ref {number} fin") == f"Ref {' ' * 40} fin"
