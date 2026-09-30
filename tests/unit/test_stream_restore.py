"""StreamRestorer: restores streamed text on the fly, holding back only a possible placeholder."""

import pytest

from antifaz import mask
from antifaz.detect.types import EntityType
from antifaz.restore import MAX_HOLDBACK, StreamLimitExceeded, StreamRestorer, restore
from antifaz.vault import Vault

DNI = "12345678Z"  # synthetic, checksum-valid


def _vault_with_dni() -> tuple[str, Vault]:
    result = mask(f"DNI {DNI}")
    assert result.text == "DNI [[ES_DNI_1]]"
    return result.text, result.vault


def _stream(chunks: list[str]) -> tuple[list[str], str]:
    _, vault = _vault_with_dni()
    restorer = StreamRestorer(vault)
    outs = [restorer.feed(chunk) for chunk in chunks]
    return outs, restorer.flush()


def test_plain_text_is_emitted_at_once() -> None:
    outs, rest = _stream(["hola ", "mundo"])
    assert outs == ["hola ", "mundo"]
    assert rest == ""


def test_a_placeholder_split_in_pieces_is_restored_when_complete() -> None:
    outs, rest = _stream(["Tu DNI es [", "[ES_D", "NI_1", "]", "] vale"])
    assert outs == ["Tu DNI es ", "", "", "", f"{DNI} vale"]
    assert rest == ""


@pytest.mark.parametrize(
    "tail", ["[", "[[", "[[ES_D", "[[ es_dni_1 ", "[[ES_DNI_", "[[ES_DNI_1", "[[ES_DNI_1]"]
)
def test_only_a_possible_placeholder_is_held_back(tail: str) -> None:
    outs, rest = _stream(["texto ", tail])
    assert outs[1] == ""
    assert rest == tail  # a cut stream shows the prefix as it is: it is a placeholder, not data


@pytest.mark.parametrize(
    "tail", ["[[!", "[[x y", "[[ES_1_", "[[_A", "[[ES__", "[[1", "[[ES_1 x", "]"]
)
def test_text_that_cannot_become_a_placeholder_is_not_held(tail: str) -> None:
    _, vault = _vault_with_dni()
    restorer = StreamRestorer(vault)
    assert restorer.feed(tail) == restore(tail, vault)
    assert restorer.flush() == ""


def test_escape_split_across_chunks_loses_its_bang() -> None:
    outs, rest = _stream(["a [[", "!b"])
    assert "".join(outs) + rest == "a [[b"


def test_long_bracket_run_keeps_only_two_brackets() -> None:
    outs, rest = _stream(["[" * 500, "!"])
    assert outs[0] == "[" * 498
    assert "".join(outs) + rest == "[" * 500


def test_bracket_run_before_a_placeholder() -> None:
    outs, rest = _stream(["[[[", "ES_DNI_1]]"])
    assert "".join(outs) + rest == f"[{DNI}"


def test_case_and_spaces_are_tolerated_like_restore() -> None:
    outs, rest = _stream(["[[ es_", "Dni_1\t]", "]"])
    assert "".join(outs) + rest == DNI


def test_unknown_placeholder_stays_as_it_is() -> None:
    outs, rest = _stream(["[[ES_DNI_2]]", " y [[EMAIL_1]]"])
    assert "".join(outs) + rest == "[[ES_DNI_2]] y [[EMAIL_1]]"


def test_restored_values_are_not_scanned_again() -> None:
    result = mask("correo [[ES_DNI_1]]")  # the user's own text, escaped by mask
    restorer = StreamRestorer(result.vault)
    out = restorer.feed(result.text) + restorer.flush()
    assert out == "correo [[ES_DNI_1]]"


def test_non_ascii_look_alikes_are_not_letters() -> None:
    # Kelvin sign and dotless i would match [A-Za-z] under IGNORECASE without ASCII.
    for tail in ["[[ES_DNI_" + chr(0x131), "[[" + chr(0x212A)]:
        _, vault = _vault_with_dni()
        restorer = StreamRestorer(vault)
        assert restorer.feed(tail) == tail


def test_holdback_cap_covers_the_longest_placeholder_with_spaces() -> None:
    longest = max(len(entity.value) for entity in EntityType)
    placeholder = "[[" + " " * 16 + "X" * longest + "_" + "9" * 9 + " " * 16 + "]]"
    assert len(placeholder) <= MAX_HOLDBACK


def test_holdback_cap_exceeded_raises_and_keeps_the_tail() -> None:
    _, vault = _vault_with_dni()
    restorer = StreamRestorer(vault)
    restorer.feed("ok [[")
    with pytest.raises(StreamLimitExceeded):
        restorer.feed(" " * MAX_HOLDBACK)
    assert restorer.flush() == "[[" + " " * MAX_HOLDBACK  # no data: shown as it is


def test_holdback_cap_exceeded_in_one_big_chunk() -> None:
    _, vault = _vault_with_dni()
    restorer = StreamRestorer(vault)
    with pytest.raises(StreamLimitExceeded):
        restorer.feed("x [[" + "A" * 200)


def test_flush_resets_the_restorer() -> None:
    _, vault = _vault_with_dni()
    restorer = StreamRestorer(vault)
    restorer.feed("[[ES")
    assert restorer.flush() == "[[ES"
    assert restorer.flush() == ""
    assert restorer.pending == 0
