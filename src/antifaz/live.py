"""«En directo» (issue 53, PR 11e-1, ADR-0018): the in-memory counter. Signatures only (red)."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from antifaz.detect.types import EntityType


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


@dataclass(frozen=True, slots=True)
class LiveEvent:
    origin: Origin
    client: Client
    outcome: Outcome
    reason: Reason
    provider: Provider
    at: int
    types: Mapping[EntityType, int]


@dataclass(frozen=True, slots=True)
class LiveSnapshot:
    recent: tuple[LiveEvent, ...]
    hidden: Mapping[EntityType, int]
    outcomes: Mapping[Outcome, int]
    checks: int
    uptime_seconds: int
    processes: int


def minute(seconds: float) -> int:
    raise NotImplementedError


def client_of(user_agent: bytes | str | None) -> Client:
    raise NotImplementedError


def reason_of(error: BaseException) -> Reason:
    raise NotImplementedError


def provider_of(origin: Origin) -> Provider:
    raise NotImplementedError


class Subscription:
    def drain(self) -> tuple[bool, tuple[LiveEvent, ...]]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class LiveFeed(Protocol):
    def record(self, event: LiveEvent) -> None: ...
    def snapshot(self) -> LiveSnapshot: ...
    def subscribe(
        self, notify: Callable[[], None], capacity: int = SUBSCRIBER_QUEUE
    ) -> Subscription: ...


class MemoryLiveFeed:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError


class NullLiveFeed:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError


class LiveNote:
    def __init__(self, origin: Origin, client: Client) -> None:
        raise NotImplementedError


def note_of(scope: Mapping[str, Any]) -> LiveNote | None:
    raise NotImplementedError
