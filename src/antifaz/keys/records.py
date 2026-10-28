"""Key records (stub)."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, StrEnum

DEFAULT_LIFETIME = timedelta(days=90)
MAX_NAME_LENGTH = 64
HASH_BYTES = 32


class Scope(StrEnum):
    OPENAI_CHAT = "openai_chat"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    MODELS = "models"
    SCAN = "scan"


class Expiry(Enum):
    NEVER = "never"


class RefusalReason(StrEnum):
    MALFORMED = "malformed"
    UNKNOWN = "unknown"
    REVOKED = "revoked"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True, kw_only=True)
class KeyRecord:
    id: int
    name: str = field(repr=False)
    scopes: frozenset[Scope]
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    key_hash: bytes = field(repr=False, compare=False)
    pepper_version: int
    per_minute_limit: int | None = None
    per_day_limit: int | None = None


@dataclass(frozen=True, slots=True)
class Caller:
    key_id: int
    scopes: frozenset[Scope]


@dataclass(frozen=True, slots=True)
class Verified:
    caller: Caller


@dataclass(frozen=True, slots=True)
class Refused:
    reason: RefusalReason


type VerifyResult = Verified | Refused


@dataclass(frozen=True, slots=True)
class CreatedKey:
    key: str = field(repr=False)
    record: KeyRecord
