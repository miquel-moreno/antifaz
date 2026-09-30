"""The SSE parser gives the same events however the stream is cut (CR, LF and CRLF included)."""

from itertools import pairwise

from hypothesis import given, settings
from hypothesis import strategies as st

from antifaz.providers.sse import SSEParser, format_event

values = st.text(alphabet=st.sampled_from(list('ab :{}"[]!')), max_size=12)
events = st.lists(st.tuples(st.none() | st.sampled_from(["x", "ping"]), values), max_size=6)
line_ends = st.sampled_from(["\n", "\r\n", "\r"])


def _parse(chunks: list[str]) -> list[tuple[str | None, str | None, str]]:
    parser = SSEParser()
    out = []
    for chunk in chunks:
        out.extend((e.event, e.data, e.raw) for e in parser.feed(chunk))
    assert parser.finished
    return out


@settings(max_examples=400, deadline=None)
@given(items=events, eol=line_ends, data=st.data())
def test_any_chunking_gives_the_same_events(
    items: list[tuple[str | None, str]], eol: str, data: st.DataObject
) -> None:
    text = "".join(format_event(name, value) for name, value in items).replace("\n", eol)
    points = sorted(data.draw(st.sets(st.integers(0, len(text)), max_size=10)))
    bounds = [0, *points, len(text)]

    whole = _parse([text])
    assert [(e, d) for e, d, _ in whole] == items
    parts = _parse([text[a:b] for a, b in pairwise(bounds)])
    assert [(e, d) for e, d, _ in parts] == items
    # CRLF cut in two may leave the LF with the next event's text; the fields never change.
    if eol != "\r\n":
        assert parts == whole
