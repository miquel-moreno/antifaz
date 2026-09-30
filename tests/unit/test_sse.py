"""Server-sent events: an incremental parser and a serializer with size limits."""

import pytest

from antifaz.providers.sse import (
    MAX_EVENT_CHARS,
    MAX_LINE_CHARS,
    MalformedStream,
    SSEParser,
    format_event,
    replace_data,
)


def _parse(*chunks: str) -> list:  # type: ignore[type-arg]
    parser = SSEParser()
    events = []
    for chunk in chunks:
        events.extend(parser.feed(chunk))
    return events


def test_event_and_data_lines() -> None:
    (event,) = _parse('event: ping\ndata: {"type": "ping"}\n\n')
    assert event.event == "ping"
    assert event.data == '{"type": "ping"}'
    assert event.raw == 'event: ping\ndata: {"type": "ping"}\n\n'


def test_data_without_space_and_leading_space_rules() -> None:
    (event,) = _parse("data:x\ndata:  y\n\n")
    assert event.data == "x\n y"  # one space after the colon is removed, not two


@pytest.mark.parametrize("eol", ["\n", "\r\n", "\r"])
def test_every_line_ending(eol: str) -> None:
    (event,) = _parse(f"data: a{eol}data: b{eol}{eol}")
    assert event.data == "a\nb"


def test_crlf_split_between_chunks_is_one_line_end() -> None:
    events = _parse("data: a\r", "\n\r", "\n")
    assert [e.data for e in events] == ["a"]


def test_event_split_in_many_chunks() -> None:
    text = 'event: message_stop\ndata: {"type": "message_stop"}\n\n'
    events = _parse(*text)  # one character at a time
    assert len(events) == 1
    assert events[0].raw == text


def test_multi_line_data() -> None:
    (event,) = _parse("data: uno\ndata: dos\ndata:\n\n")
    assert event.data == "uno\ndos\n"


def test_comments_are_kept_as_their_own_event() -> None:
    (event,) = _parse(": keep-alive\n\n")
    assert event.data is None
    assert event.event is None
    assert event.fields == (("", " keep-alive"),)
    assert event.raw == ": keep-alive\n\n"


def test_unknown_fields_are_kept() -> None:
    (event,) = _parse("id: 7\nretry: 100\nfoo\ndata: x\n\n")
    assert event.fields == (("id", "7"), ("retry", "100"), ("foo", ""), ("data", "x"))


def test_blank_lines_alone_are_no_event() -> None:
    assert _parse("\n\n\r\n") == []


def test_no_data_field_means_data_is_none() -> None:
    (event,) = _parse("event: x\n\n")
    assert event.data is None


def test_unfinished_event_is_reported_at_close() -> None:
    parser = SSEParser()
    assert parser.feed("data: a") == []
    assert parser.finished is False
    parser.feed("\n\n")
    assert parser.finished is True


def test_line_too_long_is_malformed() -> None:
    parser = SSEParser()
    with pytest.raises(MalformedStream) as info:
        parser.feed("data: " + "a" * MAX_LINE_CHARS)
    assert "aaaa" not in str(info.value)


def test_event_too_big_is_malformed() -> None:
    parser = SSEParser()
    line = "data: " + "a" * (MAX_LINE_CHARS // 2) + "\n"
    with pytest.raises(MalformedStream):
        for _ in range(MAX_EVENT_CHARS // len(line) + 2):
            parser.feed(line)


def test_replace_data_keeps_other_fields() -> None:
    (event,) = _parse("event: content_block_delta\nid: 3\ndata: {}\n\n")
    assert replace_data(event, '{"a": 1}\n{"b": 2}') == (
        'event: content_block_delta\nid: 3\ndata: {"a": 1}\ndata: {"b": 2}\n\n'
    )


def test_format_event() -> None:
    assert format_event(None, "[DONE]") == "data: [DONE]\n\n"
    assert format_event("error", "{}") == "event: error\ndata: {}\n\n"


def test_serialized_event_parses_back_the_same() -> None:
    text = format_event("x", " leading space\nsecond")
    (event,) = _parse(text)
    assert event.event == "x"
    assert event.data == " leading space\nsecond"


def test_a_bom_at_the_start_of_the_stream_is_dropped() -> None:
    parser = SSEParser()
    assert parser.feed(chr(0xFEFF)) == []
    (event,) = parser.feed("data: x\n\n")
    assert event.fields == (("data", "x"),)
    (again,) = parser.feed(chr(0xFEFF) + "data: y\n\n")  # only at the very start
    assert again.fields == ((chr(0xFEFF) + "data", "y"),)
