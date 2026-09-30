"""Invariant 3: the restored text does not depend on how the stream is cut into chunks.

For any masked text and any chunking, concat(feed(chunk)) + flush() == restore(text).
The texts mix synthetic values, their placeholders, escapes, unknown placeholders, spaces,
tabs and case variants: everything that looks like the start of a placeholder.
"""

import re
from itertools import pairwise

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from antifaz import mask
from antifaz.restore import StreamRestorer, restore

# Synthetic values with valid check digits: never real data.
VALUES = ["12345678Z", "87654321X", "ana@example.com", "ES9121000418450200051332"]
TRICKY = [
    "[[ES_DNI_1]]",
    "[[ es_dni_1 ]]",
    "[[\tEs_Dni_2\t]]",
    "[[EMAIL_1]]",
    "[[ES_DNI_9]]",
    "[[!",
    "[[[",
    "[",
    "]]",
    "!",
    "[[ES_DNI_",
    "[[x]]",
    "_1",
    "[[" + "A" * 70,
    "[[Pagina_De_Ejemplo_Muy_Larga_Sin_Espacios_Para_Pasar_El_Tope_De_Retencion]]",
    "[[ES_DNI_" + "1" * 70,
]
adversarial = st.text(alphabet=st.sampled_from(list("[]! \t_aEsX019")), max_size=20)
pieces = st.one_of(adversarial, st.sampled_from(VALUES), st.sampled_from(TRICKY))
texts = st.lists(pieces, max_size=14).map("".join) | st.text(max_size=60)
# Documented limit (unit tests): more than MAX_HOLDBACK characters held after "[[" can only
# come from a very long run of spaces or tabs, and are let go. Only that is kept out; long
# tokens (letters, digits) are part of the property.
_LONG_BLANKS = re.compile(r"[ \t]{30,}")


@st.composite
def cuts(draw: st.DrawFn, text: str) -> list[str]:
    points = sorted(draw(st.sets(st.integers(0, len(text)), max_size=12)))
    bounds = [0, *points, len(text)]
    return [text[a:b] for a, b in pairwise(bounds)]


@settings(max_examples=600, deadline=None)
@given(data=st.data(), original=texts, masked_already=st.booleans())
def test_any_chunking_gives_the_same_text_as_restore(
    data: st.DataObject, original: str, masked_already: bool
) -> None:
    result = mask(f"DNI 12345678Z, otro 87654321X {original}")
    # The provider may answer with any text: our masked text, or raw text with fake markers.
    answer = result.text if masked_already else original + result.text[:20] + original
    assume(not _LONG_BLANKS.search(answer))
    chunks = data.draw(cuts(answer))

    restorer = StreamRestorer(result.vault)
    streamed = "".join(restorer.feed(chunk) for chunk in chunks) + restorer.flush()

    assert streamed == restore(answer, result.vault)


@settings(max_examples=300, deadline=None)
@given(data=st.data(), original=texts)
def test_one_character_at_a_time(data: st.DataObject, original: str) -> None:
    result = mask(f"{original} 12345678Z")
    assume(not _LONG_BLANKS.search(result.text))
    restorer = StreamRestorer(result.vault)
    streamed = "".join(restorer.feed(char) for char in result.text) + restorer.flush()
    assert streamed == restore(result.text, result.vault) == f"{original} 12345678Z"
