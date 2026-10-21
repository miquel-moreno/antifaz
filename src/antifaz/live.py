"""«En directo» (issue 53, PR 11e-1, ADR-0018): what the gateway did, in memory, by type only.

A LiveEvent is made ONLY of enums, integers and a time rounded down to the minute: the origin
(which route), the client (a closed list read from the User-Agent, never the User-Agent
itself), the outcome, why it was blocked, the provider and, per entity type, how many distinct
values were hidden (allowed types, such as ES_CIF, are never counted). There is no field where
a text could go, and the constructor refuses anything that is not exactly its type.

- MemoryLiveFeed keeps the last RECENT (20) events, the totals of hidden values per type, of
  outcomes and (11e-2) of blocks by reason for the proxy routes, and apart (`tries`, `scans`)
  how many «Prueba un texto» and /antifaz/scan requests it saw: those are in the feed but never
  in the «hidden data» totals (nothing was sent anywhere). Also the uptime and how many worker
  processes the server runs, so the panel can say «datos de este proceso»: each process counts
  only its own requests.
- Subscribers (the panel's SSE streams, web/live_stream.py) get a queue of SUBSCRIBER_QUEUE
  (32) events. When it is full, it is emptied and marked: the stream sends a fresh snapshot
  instead of an ever-growing buffer.
- NullLiveFeed, when the panel is off: it keeps nothing.
- LiveNote is what one request decided, set where it is decided (after masking, in the error
  handlers) and recorded once at the end of the request (api/live_record.py). note_of() reads
  it from the ASGI scope.

Owner condition 3: the feed lives ONLY in memory. An event is never written to a file or a log;
this module has no file I/O and logs only an error's type (tests/unit/test_live.py).

record() never blocks and never awaits: a bounded deque, a few counters and a call per
subscriber (at most a handful), each wrapped so that a failing one never reaches the request.
It runs on the event loop thread, like the subscribers' wake-ups (asyncio.Event.set).

Standard library only, plus the entity types and the blocking errors. Never antifaz.web.

Forbidden: a str field in LiveEvent or LiveSnapshot. Keeping the User-Agent, a path, a value or
a marker. Logging an event, writing one to disk, or sending one anywhere but a subscriber.
"""

import logging
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol

from antifaz.detect.types import EntityType
from antifaz.errors import (
    AttachmentBlocked,
    DetectorFailed,
    EgressBlocked,
    NestingTooDeep,
    UnmaskableField,
)


class Origin(StrEnum):
    OPENAI_CHAT = "openai_chat"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    COUNT_TOKENS = "count_tokens"
    SCAN = "scan"
    TRY = "try"


class Client(StrEnum):
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    OPEN_WEBUI = "open_webui"
    OPENAI_PYTHON = "openai_python"
    ANTHROPIC_PYTHON = "anthropic_python"
    CURL = "curl"
    OTHER = "other"


class Outcome(StrEnum):
    MASKED = "masked"
    NO_DATA = "no_data"
    BLOCKED = "blocked"
    UPSTREAM_ERROR = "upstream_error"


class Reason(StrEnum):
    ATTACHMENT = "attachment"
    DETECTOR_FAILED = "detector_failed"
    EGRESS_GUARD = "egress_guard"
    UNMASKABLE_FIELD = "unmaskable_field"
    TOO_DEEP = "too_deep"
    NONE = "none"


class Provider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    NONE = "none"


RECENT = 20
SUBSCRIBER_QUEUE = 32
NOTE_KEY = "antifaz.live"
# The routes that send something to a provider: only they add to the totals.
PROXY_ORIGINS = frozenset({Origin.OPENAI_CHAT, Origin.ANTHROPIC_MESSAGES, Origin.COUNT_TOKENS})
_PROVIDERS = {
    Origin.OPENAI_CHAT: Provider.OPENAI,
    Origin.ANTHROPIC_MESSAGES: Provider.ANTHROPIC,
    Origin.COUNT_TOKENS: Provider.ANTHROPIC,
}
_REASONS: tuple[tuple[type[Exception], Reason], ...] = (
    (AttachmentBlocked, Reason.ATTACHMENT),
    (DetectorFailed, Reason.DETECTOR_FAILED),
    (EgressBlocked, Reason.EGRESS_GUARD),
    (UnmaskableField, Reason.UNMASKABLE_FIELD),
    (NestingTooDeep, Reason.TOO_DEEP),
)
# Exact, case-sensitive prefixes of known clients' User-Agent, checked in order. Only the first
# _UA_PREFIX characters are read: a longer header is never scanned for a name.
_CLIENTS: tuple[tuple[str, Client], ...] = (
    ("claude-cli/", Client.CLAUDE_CODE),
    ("codex_cli_rs/", Client.CODEX),
    ("open-webui/", Client.OPEN_WEBUI),
    ("OpenAI/Python", Client.OPENAI_PYTHON),
    ("Anthropic/Python", Client.ANTHROPIC_PYTHON),
    ("curl/", Client.CURL),
)
_UA_PREFIX = 32

logger = logging.getLogger("antifaz.live")


def minute(seconds: float) -> int:
    """`seconds` (Unix time) rounded down to the minute, as an int (owner decision)."""
    return int(seconds // 60) * 60


def client_of(user_agent: bytes | str | None) -> Client:
    """The known client a User-Agent names by its prefix, else OTHER. Nothing of it is kept."""
    if isinstance(user_agent, bytes):
        user_agent = user_agent[:_UA_PREFIX].decode("ascii", "replace")
    head = (user_agent or "")[:_UA_PREFIX]
    for prefix, client in _CLIENTS:
        if head.startswith(prefix):
            return client
    return Client.OTHER


def reason_of(error: BaseException) -> Reason:
    """Why a request was blocked, by the error's class (antifaz.errors); NONE if unknown."""
    for kind, reason in _REASONS:
        if isinstance(error, kind):
            return reason
    return Reason.NONE


def provider_of(origin: Origin) -> Provider:
    return _PROVIDERS.get(origin, Provider.NONE)


def _counts(types: Mapping[EntityType, int]) -> Mapping[EntityType, int]:
    """A read-only copy in EntityType order; every key an EntityType, every count an int > 0."""
    for entity, count in types.items():
        if not isinstance(entity, EntityType) or type(count) is not int or count < 1:
            raise TypeError("types holds EntityType -> int (1 or more)")
    return MappingProxyType({entity: types[entity] for entity in EntityType if entity in types})


def _check(value: object, kind: type[StrEnum]) -> None:
    if type(value) is not kind:
        raise TypeError(f"expected {kind.__name__}")


@dataclass(frozen=True, slots=True)
class LiveEvent:
    """One request, by type only. `at` is Unix time rounded down to the minute."""

    origin: Origin
    client: Client
    outcome: Outcome
    reason: Reason
    provider: Provider
    at: int
    types: Mapping[EntityType, int]

    def __post_init__(self) -> None:
        for name, kind in (
            ("origin", Origin),
            ("client", Client),
            ("outcome", Outcome),
            ("reason", Reason),
            ("provider", Provider),
        ):
            _check(getattr(self, name), kind)
        if type(self.at) is not int or self.at < 0 or self.at % 60:
            raise ValueError("at is a whole minute of Unix time")
        object.__setattr__(self, "types", _counts(self.types))


@dataclass(frozen=True, slots=True)
class LiveSnapshot:
    """The feed now: the recent events (newest first), the proxy's totals (hidden values per
    type, every outcome, blocks per reason), the Try and the scan counts, whole seconds since the
    feed started and how many processes the server runs. The blocks add up to the blocked
    outcome; `hidden` and `blocks` hold only counts of 1 or more, in their enum's order."""

    recent: tuple[LiveEvent, ...]
    hidden: Mapping[EntityType, int]
    outcomes: Mapping[Outcome, int]
    blocks: Mapping[Reason, int]
    tries: int
    scans: int
    uptime_seconds: int
    processes: int


def _processes(processes: object) -> int:
    if type(processes) is not int:
        raise TypeError("processes is an int")
    if processes < 1:
        raise ValueError("at least one process")
    return processes


class Subscription:
    """A subscriber's bounded queue. drain() returns (overflowed, events) and empties it: when
    the queue filled up it holds nothing and `overflowed` is True (send a fresh snapshot)."""

    __slots__ = ("_capacity", "_events", "_notify", "_overflowed", "_unsubscribe")

    def __init__(
        self,
        notify: Callable[[], None],
        capacity: int,
        unsubscribe: Callable[["Subscription"], None],
    ) -> None:
        self._notify: Callable[[], None] | None = notify
        self._capacity = capacity
        self._events: list[LiveEvent] = []
        self._overflowed = False
        self._unsubscribe = unsubscribe

    def _push(self, event: LiveEvent) -> None:
        if self._notify is None:
            return
        if self._overflowed or len(self._events) >= self._capacity:
            self._events.clear()  # nothing more is kept: the stream sends a snapshot
            self._overflowed = True
        else:
            self._events.append(event)
        self._notify()

    def drain(self) -> tuple[bool, tuple[LiveEvent, ...]]:
        overflowed, events = self._overflowed, tuple(self._events)
        self._overflowed = False
        self._events.clear()
        return overflowed, events

    def close(self) -> None:
        if self._notify is not None:
            self._notify = None
            self._events.clear()
            self._overflowed = False
            self._unsubscribe(self)


class LiveFeed(Protocol):
    """The live counter (ADR-0018). In memory today; the evidence log (item 9) can take it."""

    def record(self, event: LiveEvent) -> None:
        """Keep one event and wake the subscribers. Never blocks."""
        ...

    def snapshot(self) -> LiveSnapshot: ...

    def subscribe(
        self, notify: Callable[[], None], capacity: int = SUBSCRIBER_QUEUE
    ) -> Subscription:
        """A bounded queue of the next events; `notify()` is called after each one."""
        ...


class MemoryLiveFeed:
    """LiveFeed in this process's memory: it starts empty with every process start."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, processes: int = 1) -> None:
        self._clock = clock
        self._started = clock()
        self._processes = _processes(processes)
        self._recent: deque[LiveEvent] = deque(maxlen=RECENT)
        self._hidden: dict[EntityType, int] = {}
        self._outcomes: dict[Outcome, int] = dict.fromkeys(Outcome, 0)
        self._blocks: dict[Reason, int] = dict.fromkeys(Reason, 0)
        self._tries = 0
        self._scans = 0
        self._subscribers: list[Subscription] = []

    def record(self, event: LiveEvent) -> None:
        if not isinstance(event, LiveEvent):
            raise TypeError("only a LiveEvent is recorded")
        self._recent.append(event)
        if event.origin in PROXY_ORIGINS:
            self._outcomes[event.outcome] += 1
            if event.outcome is Outcome.BLOCKED:
                self._blocks[event.reason] += 1
            for entity, count in event.types.items():
                self._hidden[entity] = self._hidden.get(entity, 0) + count
        elif event.origin is Origin.TRY:
            self._tries += 1
        else:
            self._scans += 1
        for subscription in list(self._subscribers):
            try:
                subscription._push(event)
            except Exception as error:
                logger.error("live feed subscriber failed: %s", type(error).__name__)

    def snapshot(self) -> LiveSnapshot:
        return LiveSnapshot(
            recent=tuple(reversed(self._recent)),
            hidden=_counts(self._hidden),
            outcomes=MappingProxyType(dict(self._outcomes)),
            blocks=MappingProxyType({r: n for r, n in self._blocks.items() if n}),
            tries=self._tries,
            scans=self._scans,
            uptime_seconds=max(0, int(self._clock() - self._started)),
            processes=self._processes,
        )

    def subscribe(
        self, notify: Callable[[], None], capacity: int = SUBSCRIBER_QUEUE
    ) -> Subscription:
        subscription = Subscription(notify, capacity, self._subscribers.remove)
        self._subscribers.append(subscription)
        return subscription


class NullLiveFeed:
    """LiveFeed when the panel is off: nothing is kept and nobody is told."""

    def __init__(self, *, processes: int = 1) -> None:
        self._processes = _processes(processes)

    def record(self, event: LiveEvent) -> None:
        """Nothing is kept."""

    def snapshot(self) -> LiveSnapshot:
        return LiveSnapshot(
            recent=(),
            hidden=MappingProxyType({}),
            outcomes=MappingProxyType(dict.fromkeys(Outcome, 0)),
            blocks=MappingProxyType({}),
            tries=0,
            scans=0,
            uptime_seconds=0,
            processes=self._processes,
        )

    def subscribe(
        self, notify: Callable[[], None], capacity: int = SUBSCRIBER_QUEUE
    ) -> Subscription:
        subscription = Subscription(notify, capacity, lambda _: None)
        subscription.close()  # closed from the start: it never gets anything
        return subscription


@dataclass(eq=False)
class LiveNote:
    """What one request decided, until it is recorded. Without a decision, nothing is."""

    origin: Origin
    client: Client
    outcome: Outcome | None = None
    reason: Reason = Reason.NONE
    types: Mapping[EntityType, int] = field(default_factory=dict, repr=False)

    def masked(self, types: Mapping[EntityType, int]) -> None:
        """After masking: distinct hidden values per type (types with 0 left out)."""
        kept = {entity: count for entity, count in types.items() if count}
        self.types = kept
        self.outcome = Outcome.MASKED if kept else Outcome.NO_DATA

    def blocked(self, reason: Reason) -> None:
        """Blocked: nothing was sent, so nothing counts as hidden."""
        self.outcome, self.reason, self.types = Outcome.BLOCKED, reason, {}

    def upstream_error(self) -> None:
        """Masked and sent, and the provider failed: what was hidden still counts."""
        self.outcome = Outcome.UPSTREAM_ERROR

    def event(self, at: float) -> LiveEvent | None:
        if self.outcome is None:
            return None
        return LiveEvent(
            origin=self.origin,
            client=self.client,
            outcome=self.outcome,
            reason=self.reason,
            provider=provider_of(self.origin),
            at=minute(at),
            types=self.types,
        )


def note_of(scope: Mapping[str, Any]) -> LiveNote | None:
    """The request's LiveNote in scope["state"], or None (not a recorded route)."""
    state = scope.get("state")
    note = state.get(NOTE_KEY) if isinstance(state, Mapping) else None
    return note if isinstance(note, LiveNote) else None


__all__ = [
    "NOTE_KEY",
    "PROXY_ORIGINS",
    "RECENT",
    "SUBSCRIBER_QUEUE",
    "Client",
    "LiveEvent",
    "LiveFeed",
    "LiveNote",
    "LiveSnapshot",
    "MemoryLiveFeed",
    "NullLiveFeed",
    "Origin",
    "Outcome",
    "Provider",
    "Reason",
    "Subscription",
    "client_of",
    "minute",
    "note_of",
    "provider_of",
    "reason_of",
]
