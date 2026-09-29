"""Invariant 2 of the spec: with the egress guard OFF, the fake provider receives no value the
policy said to hide. The guard must not hide bugs of the masker, so the first test never calls it.

Also: the guard never blocks the masker's own output, and always blocks the unmasked text.
"""

import json
import unicodedata

from hypothesis import given, settings
from hypothesis import strategies as st

from antifaz import EgressBlocked, mask
from antifaz.detect.validators._ascii import compact
from antifaz.guard import check

# Synthetic values with valid check digits: never real data.
VALUES = [
    "12345678Z",
    "87654321X",
    "X1234567L",
    "ana@example.com",
    "ES9121000418450200051332",
    "612 345 678",
]
FILLER = ["Hola", "mi dato es", "gracias", "y", "[[ES_DNI_1]]", "semana", ",", "\n", "ñandú"]

turns = st.lists(
    st.lists(st.sampled_from(VALUES) | st.sampled_from(FILLER), min_size=1, max_size=8).map(
        " ".join
    ),
    min_size=1,
    max_size=5,
)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


class FakeProvider:
    """Records the exact bytes it would receive."""

    def __init__(self) -> None:
        self.received: list[bytes] = []

    def send(self, body: bytes) -> None:
        self.received.append(body)


def _body(texts: tuple[str, ...], ensure_ascii: bool) -> bytes:
    messages = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": t} for i, t in enumerate(texts)
    ]
    payload = {"model": "fake-model", "messages": messages}
    return json.dumps(payload, ensure_ascii=ensure_ascii).encode("utf-8")


@settings(max_examples=300, deadline=None)
@given(conversation=turns, ensure_ascii=st.booleans())
def test_provider_never_receives_a_hidden_value_without_the_guard(
    conversation: list[str], ensure_ascii: bool
) -> None:
    result = mask(conversation)
    provider = FakeProvider()
    provider.send(_body(result.texts, ensure_ascii))
    raw = provider.received[0]
    decoded = json.loads(raw)
    strings = [m["content"] for m in decoded["messages"]]
    haystacks = [raw.decode("utf-8"), *strings]
    for _, value in result.vault._hidden_values():
        for text in haystacks:
            assert value not in text
            assert _norm(value) not in _norm(text)
            if len(compact(value)) >= 6 and "@" not in value:
                assert compact(value) not in compact(text)


@settings(max_examples=300, deadline=None)
@given(conversation=turns, ensure_ascii=st.booleans())
def test_guard_passes_masked_output_and_blocks_the_original(
    conversation: list[str], ensure_ascii: bool
) -> None:
    result = mask(conversation)
    check(_body(result.texts, ensure_ascii), result.vault)
    if len(result.vault):
        try:
            check(_body(tuple(conversation), ensure_ascii), result.vault)
        except EgressBlocked:
            pass
        else:
            raise AssertionError("the unmasked conversation was not blocked")
