"""placeholder_parts() (issue 53, PR 11d-a): a text cut at its placeholders, with restore()'s
own pattern, so «Prueba un texto» can show each marker apart without a second pattern.

Every part is (text, None) for text kept as it is (escapes `[[!` included, untouched) or
(raw placeholder, TOKEN) for a placeholder, its token in upper case. Joining the parts gives the
text back, and restoring the parts one by one gives the same as restore() of the whole text.
"""

from hypothesis import given
from hypothesis import strategies as st

from antifaz import mask, restore
from antifaz.restore import placeholder_parts, placeholder_tokens
from tests.conftest import SENTINEL_DNI


def test_text_and_placeholders_apart() -> None:
    parts = placeholder_parts("Hola [[ES_DNI_1]], y [[ email_2 ]].")

    assert parts == [
        ("Hola ", None),
        ("[[ES_DNI_1]]", "ES_DNI_1"),
        (", y ", None),
        ("[[ email_2 ]]", "EMAIL_2"),
        (".", None),
    ]


def test_escapes_stay_inside_the_text_parts() -> None:
    parts = placeholder_parts("a [[!ES_DNI_1]] b [[[!x")

    assert parts == [("a [[!ES_DNI_1]] b [[[!x", None)]


def test_no_empty_parts() -> None:
    assert placeholder_parts("") == []
    assert placeholder_parts("[[A_1]][[B_2]]") == [("[[A_1]]", "A_1"), ("[[B_2]]", "B_2")]


def test_same_tokens_as_placeholder_tokens() -> None:
    text = "[[ES_DNI_1]] x [[!EMAIL_1]] [[iban_3]] [[A]]"

    tokens = {token for _, token in placeholder_parts(text) if token is not None}

    assert tokens == placeholder_tokens(text) == {"ES_DNI_1", "IBAN_3"}


TEXT = st.text(alphabet=st.sampled_from(list("[]!_ aZ19\t")) | st.characters(), max_size=60)


@given(TEXT)
def test_parts_join_back_and_restore_part_by_part(text: str) -> None:
    result = mask(f"{SENTINEL_DNI} {text}")
    masked = result.text

    parts = placeholder_parts(masked)

    assert "".join(raw for raw, _ in parts) == masked
    assert "".join(restore(raw, result.vault) for raw, _ in parts) == restore(masked, result.vault)
