"""«En directo»'s counter (issue 53, PR 11e-1, ADR-0018): antifaz/live.py on its own.

- A LiveEvent holds only enums, integers and a time rounded down to the minute: there is no
  field where a text could go, and the constructor refuses anything else.
- The client comes from a closed list of User-Agent prefixes; the raw value is never kept.
- MemoryLiveFeed keeps the last 20 events, totals per type and per outcome (only for the
  proxy: «Prueba un texto» and /antifaz/scan are counted apart), the uptime and how many worker
  processes the server runs. Subscribers get a bounded queue: past it, they are told to take a
  fresh snapshot instead of an ever-growing buffer.
- NullLiveFeed (the panel is off) keeps nothing.
- Owner condition 3: the feed lives only in memory. live.py has no file I/O and never logs an
  event (only an error's type), checked statically and at runtime.
"""

import ast
import builtins
import logging
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

import antifaz.live
from antifaz.detect.types import EntityType
from antifaz.errors import (
    AttachmentBlocked,
    DetectorFailed,
    EgressBlocked,
    NestingTooDeep,
    UnmaskableField,
)
from antifaz.live import (
    NOTE_KEY,
    RECENT,
    SUBSCRIBER_QUEUE,
    Client,
    LiveEvent,
    LiveNote,
    MemoryLiveFeed,
    NullLiveFeed,
    Origin,
    Outcome,
    Provider,
    Reason,
    client_of,
    minute,
    note_of,
    provider_of,
    reason_of,
)

LIVE = Path(antifaz.live.__file__)


def _event(
    origin: Origin = Origin.OPENAI_CHAT,
    outcome: Outcome = Outcome.MASKED,
    types: dict[EntityType, int] | None = None,
    at: int = 120,
    reason: Reason = Reason.NONE,
) -> LiveEvent:
    return LiveEvent(
        origin=origin,
        client=Client.CURL,
        outcome=outcome,
        reason=reason,
        provider=provider_of(origin),
        at=at,
        types={EntityType.ES_DNI: 1} if types is None else types,
    )


class _Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# --- The event: enums, integers and a minute ---------------------------------------------------


def test_minute_rounds_down_to_the_minute() -> None:
    assert minute(0) == 0
    assert minute(59.999) == 0
    assert minute(60) == 60
    assert minute(1_760_000_123.7) == 1_760_000_100
    assert type(minute(125.5)) is int


def test_an_event_holds_only_enums_integers_and_its_minute() -> None:
    event = _event(types={EntityType.EMAIL: 2, EntityType.ES_DNI: 1})

    assert isinstance(event.types, MappingProxyType)  # read-only
    assert list(event.types) == [EntityType.ES_DNI, EntityType.EMAIL]  # in EntityType order
    assert all(type(count) is int for count in event.types.values())
    assert set(LiveEvent.__dataclass_fields__) == {
        "origin",
        "client",
        "outcome",
        "reason",
        "provider",
        "at",
        "types",
    }
    for name in ("origin", "client", "outcome", "reason", "provider"):
        assert isinstance(getattr(event, name), Origin | Client | Outcome | Reason | Provider)
    assert type(event.at) is int and event.at % 60 == 0
    # Frozen with slots: no new attribute (Python raises TypeError or AttributeError for it).
    with pytest.raises((AttributeError, TypeError)):
        event.extra = "free text"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "changes",
    [
        {"origin": "openai_chat"},  # a plain string, not the enum
        {"client": "Mozilla/5.0 (X11) 12345678Z"},
        {"outcome": "masked"},
        {"reason": "none"},
        {"provider": "openai"},
        {"at": 61},  # not on the minute
        {"at": 60.0},  # not an int
        {"at": True},
        {"at": -60},
        {"types": {"ES_DNI": 1}},  # a string key
        {"types": {EntityType.ES_DNI: "1"}},
        {"types": {EntityType.ES_DNI: True}},
        {"types": {EntityType.ES_DNI: 0}},  # a type with no value is left out, never 0
        {"types": {EntityType.ES_DNI: -1}},
    ],
)
def test_an_event_refuses_anything_that_is_not_its_enum_or_an_integer(
    changes: dict[str, Any],
) -> None:
    values: dict[str, Any] = {
        "origin": Origin.SCAN,
        "client": Client.OTHER,
        "outcome": Outcome.NO_DATA,
        "reason": Reason.NONE,
        "provider": Provider.NONE,
        "at": 0,
        "types": {},
        **changes,
    }
    with pytest.raises((TypeError, ValueError)):
        LiveEvent(**values)


def test_the_provider_follows_the_origin() -> None:
    assert provider_of(Origin.OPENAI_CHAT) is Provider.OPENAI
    assert provider_of(Origin.ANTHROPIC_MESSAGES) is Provider.ANTHROPIC
    assert provider_of(Origin.COUNT_TOKENS) is Provider.ANTHROPIC
    assert provider_of(Origin.SCAN) is Provider.NONE
    assert provider_of(Origin.TRY) is Provider.NONE


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (AttachmentBlocked(), Reason.ATTACHMENT),
        (DetectorFailed(), Reason.DETECTOR_FAILED),
        (EgressBlocked(), Reason.EGRESS_GUARD),
        (UnmaskableField(), Reason.UNMASKABLE_FIELD),
        (NestingTooDeep(), Reason.TOO_DEEP),
        (ValueError("12345678Z"), Reason.NONE),
    ],
)
def test_each_block_has_its_reason(error: BaseException, reason: Reason) -> None:
    assert reason_of(error) is reason


# --- The client: a closed list, never the raw User-Agent ---------------------------------------


@pytest.mark.parametrize(
    ("user_agent", "client"),
    [
        ("claude-cli/2.0.14 (external, cli)", Client.CLAUDE_CODE),
        ("codex_cli_rs/0.46.0 (Windows 10.0.26200; x86_64) WindowsTerminal", Client.CODEX),
        ("open-webui/0.6.30", Client.OPEN_WEBUI),
        ("OpenAI/Python 2.3.0", Client.OPENAI_PYTHON),
        ("Anthropic/Python 0.69.0", Client.ANTHROPIC_PYTHON),
        ("curl/8.9.1", Client.CURL),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64)", Client.OTHER),
        ("python-httpx/0.28.1", Client.OTHER),
        ("12345678Z", Client.OTHER),
        ("", Client.OTHER),
        (None, Client.OTHER),
        (b"curl/8.0", Client.CURL),
        (b"\xff\xfe broken", Client.OTHER),
        pytest.param("x" * 10_000 + "curl/8.0", Client.OTHER, id="long-prefix"),
        ("CURL/8.0", Client.OTHER),  # exact prefixes: no folding
    ],
)
def test_the_client_comes_from_a_closed_list(
    user_agent: str | bytes | None, client: Client
) -> None:
    assert client_of(user_agent) is client


def test_a_note_keeps_the_client_never_the_user_agent() -> None:
    note = LiveNote(Origin.OPENAI_CHAT, client_of("curl/8.9.1 12345678Z"))
    note.masked({EntityType.ES_DNI: 1})
    event = note.event(at=0)

    assert event is not None
    assert "12345678Z" not in repr(event)
    assert "12345678Z" not in repr(note)


# --- The note: what one request decided --------------------------------------------------------


def test_a_note_without_a_decision_records_nothing() -> None:
    assert LiveNote(Origin.OPENAI_CHAT, Client.CURL).event(at=0) is None


def test_a_masked_note_is_masked_or_no_data_by_its_counts() -> None:
    note = LiveNote(Origin.OPENAI_CHAT, Client.CURL)
    note.masked({EntityType.ES_DNI: 2, EntityType.EMAIL: 0})
    event = note.event(at=65)
    assert event is not None
    assert event.outcome is Outcome.MASKED
    assert dict(event.types) == {EntityType.ES_DNI: 2}
    assert event.reason is Reason.NONE
    assert event.provider is Provider.OPENAI
    assert event.at == 60

    empty = LiveNote(Origin.SCAN, Client.CURL)
    empty.masked({})
    event = empty.event(at=0)
    assert event is not None and event.outcome is Outcome.NO_DATA and dict(event.types) == {}


def test_a_block_after_masking_keeps_only_the_reason() -> None:
    note = LiveNote(Origin.ANTHROPIC_MESSAGES, Client.CLAUDE_CODE)
    note.masked({EntityType.ES_DNI: 1})
    note.blocked(Reason.EGRESS_GUARD)
    event = note.event(at=0)
    assert event is not None
    assert (event.outcome, event.reason, dict(event.types)) == (
        Outcome.BLOCKED,
        Reason.EGRESS_GUARD,
        {},
    )


def test_an_upstream_error_keeps_what_was_hidden_before_sending() -> None:
    note = LiveNote(Origin.COUNT_TOKENS, Client.OTHER)
    note.masked({EntityType.IBAN: 1})
    note.upstream_error()
    event = note.event(at=0)
    assert event is not None
    assert event.outcome is Outcome.UPSTREAM_ERROR
    assert dict(event.types) == {EntityType.IBAN: 1}


def test_note_of_reads_only_a_real_note_from_the_scope_state() -> None:
    note = LiveNote(Origin.SCAN, Client.OTHER)
    assert note_of({"state": {NOTE_KEY: note}}) is note
    assert note_of({"state": {NOTE_KEY: "a note"}}) is None
    assert note_of({"state": {}}) is None
    assert note_of({}) is None


# --- The feed ----------------------------------------------------------------------------------


def test_the_feed_keeps_the_last_twenty_events_newest_first() -> None:
    feed = MemoryLiveFeed(clock=_Clock(), processes=1)
    for number in range(RECENT + 5):
        feed.record(_event(at=number * 60))

    recent = feed.snapshot().recent
    assert len(recent) == RECENT == 20
    assert [event.at for event in recent] == [n * 60 for n in range(RECENT + 4, 4, -1)]


def test_the_totals_count_the_proxy_and_never_try_or_scan() -> None:
    feed = MemoryLiveFeed(clock=_Clock(), processes=1)
    feed.record(_event(types={EntityType.ES_DNI: 2, EntityType.EMAIL: 1}))
    feed.record(_event(Origin.ANTHROPIC_MESSAGES, types={EntityType.ES_DNI: 1}))
    feed.record(_event(Origin.COUNT_TOKENS, Outcome.UPSTREAM_ERROR, {EntityType.IBAN: 1}))
    feed.record(_event(outcome=Outcome.NO_DATA, types={}))
    feed.record(_event(outcome=Outcome.BLOCKED, types={}, reason=Reason.ATTACHMENT))
    feed.record(_event(Origin.TRY, types={EntityType.ES_DNI: 5}))
    feed.record(_event(Origin.SCAN, types={EntityType.PHONE: 3}))
    feed.record(_event(Origin.SCAN, Outcome.BLOCKED, {}, reason=Reason.DETECTOR_FAILED))

    snapshot = feed.snapshot()
    assert dict(snapshot.hidden) == {EntityType.ES_DNI: 3, EntityType.EMAIL: 1, EntityType.IBAN: 1}
    assert dict(snapshot.outcomes) == {
        Outcome.MASKED: 2,
        Outcome.NO_DATA: 1,
        Outcome.BLOCKED: 1,
        Outcome.UPSTREAM_ERROR: 1,
    }
    assert snapshot.checks == 3  # «Prueba un texto» and /antifaz/scan: apart
    assert len(snapshot.recent) == 8  # every origin is in the feed


def test_the_totals_list_every_outcome_and_only_types_with_a_value() -> None:
    snapshot = MemoryLiveFeed(clock=_Clock(), processes=1).snapshot()
    assert dict(snapshot.outcomes) == dict.fromkeys(Outcome, 0)
    assert dict(snapshot.hidden) == {}
    assert snapshot.recent == ()
    assert snapshot.checks == 0


def test_the_uptime_and_the_processes_are_in_the_snapshot() -> None:
    clock = _Clock(1000.0)
    feed = MemoryLiveFeed(clock=clock, processes=3)
    clock.now = 1061.9
    snapshot = feed.snapshot()
    assert snapshot.uptime_seconds == 61
    assert snapshot.processes == 3
    assert MemoryLiveFeed(clock=clock, processes=1).snapshot().processes == 1


@pytest.mark.parametrize("processes", [0, -1, True, 1.0])
def test_the_feed_needs_a_real_number_of_processes(processes: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        MemoryLiveFeed(clock=_Clock(), processes=processes)


def test_the_feed_refuses_what_is_not_an_event() -> None:
    feed = MemoryLiveFeed(clock=_Clock(), processes=1)
    with pytest.raises(TypeError):
        feed.record("12345678Z")  # type: ignore[arg-type]
    assert feed.snapshot().recent == ()


def test_a_subscriber_gets_each_event_and_is_woken() -> None:
    feed = MemoryLiveFeed(clock=_Clock(), processes=1)
    woken: list[int] = []
    subscription = feed.subscribe(lambda: woken.append(1))
    first, second = _event(at=0), _event(at=60)
    feed.record(first)
    feed.record(second)

    assert woken == [1, 1]
    assert subscription.drain() == (False, (first, second))
    assert subscription.drain() == (False, ())


def test_a_full_subscriber_queue_asks_for_a_snapshot_instead_of_growing() -> None:
    feed = MemoryLiveFeed(clock=_Clock(), processes=1)
    subscription = feed.subscribe(lambda: None)
    for _ in range(SUBSCRIBER_QUEUE + 10):
        feed.record(_event())

    overflowed, events = subscription.drain()
    assert overflowed is True
    assert events == ()  # nothing buffered: the stream sends a fresh snapshot
    feed.record(_event())
    assert subscription.drain() == (False, (_event(),))
    assert SUBSCRIBER_QUEUE == 32


def test_a_closed_subscription_gets_nothing_more() -> None:
    feed = MemoryLiveFeed(clock=_Clock(), processes=1)
    woken: list[int] = []
    subscription = feed.subscribe(lambda: woken.append(1))
    subscription.close()
    subscription.close()  # twice is harmless
    feed.record(_event())
    assert woken == []
    assert subscription.drain() == (False, ())


def test_a_failing_subscriber_never_breaks_recording(caplog: pytest.LogCaptureFixture) -> None:
    feed = MemoryLiveFeed(clock=_Clock(), processes=1)

    def broken() -> None:
        raise RuntimeError("12345678Z")

    feed.subscribe(broken)
    told: list[int] = []
    feed.subscribe(lambda: told.append(1))
    with caplog.at_level(logging.DEBUG):
        feed.record(_event())

    assert told == [1]
    assert len(feed.snapshot().recent) == 1
    assert "12345678Z" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_the_null_feed_keeps_nothing() -> None:
    feed = NullLiveFeed(processes=2)
    woken: list[int] = []
    subscription = feed.subscribe(lambda: woken.append(1))
    feed.record(_event())

    snapshot = feed.snapshot()
    assert snapshot.recent == ()
    assert dict(snapshot.hidden) == {}
    assert snapshot.checks == 0
    assert snapshot.processes == 2
    assert woken == []
    assert subscription.drain() == (False, ())


# --- Owner condition 3: only in memory ---------------------------------------------------------

_FILE_MODULES = {
    "os",
    "io",
    "pathlib",
    "shutil",
    "tempfile",
    "pickle",
    "shelve",
    "sqlite3",
    "dbm",
    "json",
    "csv",
    "logging.handlers",
    "socket",
}
_FILE_NAMES = {"open", "write", "write_text", "write_bytes", "dump", "dumps", "print"}


def _tree() -> ast.Module:
    return ast.parse(LIVE.read_text(encoding="utf-8"), filename=str(LIVE))


def test_live_py_has_no_file_io() -> None:
    tree = _tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in _FILE_MODULES, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert node.module not in _FILE_MODULES, node.module
        elif isinstance(node, ast.Name):
            assert node.id not in _FILE_NAMES, node.id
        elif isinstance(node, ast.Attribute):
            assert node.attr not in _FILE_NAMES, node.attr


def test_live_py_logs_only_an_errors_type() -> None:
    """Every logger call has a constant message and, as arguments, only `type(x).__name__`."""
    calls = [
        node
        for node in ast.walk(_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logger"
    ]
    assert calls  # the subscriber's failure is logged: not an empty pass
    for call in calls:
        message, *args = call.args
        assert isinstance(message, ast.Constant) and isinstance(message.value, str)
        assert not call.keywords
        for arg in args:
            assert isinstance(arg, ast.Attribute) and arg.attr == "__name__", ast.dump(arg)
            assert isinstance(arg.value, ast.Call) and getattr(arg.value.func, "id", "") == "type"


def test_live_py_imports_only_the_standard_library_and_core_pieces() -> None:
    """Core, standard library only: never the panel (antifaz.web), a framework or the API."""
    allowed_own = {"antifaz.detect.types", "antifaz.errors"}
    for node in ast.walk(_tree()):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        for module in modules:
            if module.split(".")[0] == "antifaz":
                assert module in allowed_own, module
            else:
                assert module.split(".")[0] in sys.stdlib_module_names, module


def test_recording_and_reading_never_touch_a_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("the live feed touched a file")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(Path, "write_text", refuse)
    monkeypatch.setattr(Path, "write_bytes", refuse)
    monkeypatch.setattr(Path, "open", refuse)
    feed = MemoryLiveFeed(processes=1)
    subscription = feed.subscribe(lambda: None)
    for origin in Origin:
        feed.record(_event(origin))
    feed.snapshot()
    subscription.drain()
    subscription.close()
