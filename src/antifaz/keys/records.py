"""What a store keeps about a virtual key, and what verify() answers (ADR-0020).

- KeyRecord: a fixed number (`id`, never reused: keys are revoked, never deleted), a name for
  the admin, the scopes, the UTC times (created, expires, revoked), the HMAC of the key and the
  pepper version it used, and the per-minute and per-day request limits (only stored in 8a;
  counting comes in 8e). Closed: every field is checked when it is built, and it cannot be
  copied or pickled (that would skip the checks or lose the hash). Its repr shows neither the
  name nor the hash, and equality never compares the hash.
- Caller: who is calling, for the door and, later, policies: the key's number and scopes. No
  clear key, no hash, no name.
- Verified(caller) or Refused(reason), with a closed list of reasons. Every refusal gets the
  same 401 at the door (8d); the reason is only for the log (`key=12 reason=revoked`).
- CreatedKey: the clear key, ONCE, with its record. Its repr hides the key and it cannot be
  pickled or copied.

Times are aware datetimes in UTC (`tzinfo is UTC`). A key works while now < expires_at: at
expires_at exactly it is expired. `expires_at is None` (no expiry) only when asked with
Expiry.NEVER; the default lifetime is DEFAULT_LIFETIME (90 days).

Scopes, one per group of routes (the mapping to routes is the door's, 8d):
- OPENAI_CHAT: POST /v1/chat/completions
- ANTHROPIC_MESSAGES: POST /v1/messages and POST /v1/messages/count_tokens
- MODELS: GET /v1/models
- SCAN: POST /antifaz/scan
"""

import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum, StrEnum
from typing import Any, NoReturn, TypeGuard

DEFAULT_LIFETIME = timedelta(days=90)
MAX_NAME_LENGTH = 64
HASH_BYTES = 32  # SHA-256
# Common caps of every store (8b-1, D14): what PostgreSQL can hold, so the memory store and the
# PostgreSQL store refuse exactly the same records. Limits are `integer` columns, the pepper
# version a `smallint`.
MAX_LIMIT = 2_147_483_647
MAX_PEPPER_VERSION = 32_767
# Unicode categories never allowed in a key name: controls (Cc), invisible format characters
# such as the bidi controls and the zero-width ones (Cf), line and paragraph separators (Zl, Zp),
# surrogates (Cs), private use (Co) and unassigned code points (Cn).
FORBIDDEN_NAME_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Cs", "Co", "Cn"})
# Unicode 15.0.0 DerivedCoreProperties.txt, Default_Ignorable_Code_Point (4174 code points);
# matches unicodedata.unidata_version of Python 3.12. Python has no API for this property, so
# the ranges are copied here verbatim; a test fails when Python's Unicode version changes.
# Some of them are not Cf or Cn (U+034F, U+115F, U+3164, U+FFA0, the variation selectors...),
# so the category check alone would miss them.
_DEFAULT_IGNORABLE_RANGES = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180D),
    (0x180E, 0x180E),
    (0x180F, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x2064),
    (0x2065, 0x2065),
    (0x2066, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0000),
    (0xE0001, 0xE0001),
    (0xE0002, 0xE001F),
    (0xE0020, 0xE007F),
    (0xE0080, 0xE00FF),
    (0xE0100, 0xE01EF),
    (0xE01F0, 0xE0FFF),
)
DEFAULT_IGNORABLE = frozenset(
    point for first, last in _DEFAULT_IGNORABLE_RANGES for point in range(first, last + 1)
)
# Not default-ignorable, but it looks like a blank: a name that seems empty or spaced.
BRAILLE_PATTERN_BLANK = 0x2800
_NAME_LENGTH_ERROR = (
    f"a key name must be 1 to {MAX_NAME_LENGTH} printable characters, "
    "without whitespace at either end"
)
_NAME_INVISIBLE_ERROR = "a key name cannot hold control or invisible characters"


class Scope(StrEnum):
    OPENAI_CHAT = "openai_chat"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    MODELS = "models"
    SCAN = "scan"


class Expiry(Enum):
    """The only way to ask for a key without expiry: `expires_in=Expiry.NEVER`."""

    NEVER = "never"


class RefusalReason(StrEnum):
    MALFORMED = "malformed"  # not afz_ + 43 + checksum: refused before any lookup
    UNKNOWN = "unknown"  # well formed, but no key has that hash (or its pepper is gone)
    REVOKED = "revoked"
    EXPIRED = "expired"


def is_positive_int(value: object) -> TypeGuard[int]:
    return type(value) is int and value >= 1


def is_utc(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is UTC


def _is_invisible(character: str) -> bool:
    point = ord(character)
    return (
        point in DEFAULT_IGNORABLE
        or point == BRAILLE_PATTERN_BLANK
        or unicodedata.category(character) in FORBIDDEN_NAME_CATEGORIES
    )


def _name_problem(name: object) -> Exception | None:
    """What is wrong with the name, or None. Its frame is gone before anything is raised."""
    if type(name) is not str:
        return TypeError("a key name must be text")
    if not 1 <= len(name) <= MAX_NAME_LENGTH:  # first: never scan a huge text
        return ValueError(_NAME_LENGTH_ERROR)
    if any(_is_invisible(character) for character in name):
        return ValueError(_NAME_INVISIBLE_ERROR)
    if name != name.strip() or not name.isprintable():
        return ValueError(_NAME_LENGTH_ERROR)
    return None


def check_name(name: object) -> None:
    """1 to MAX_NAME_LENGTH printable characters (the length is checked first), without
    whitespace at either end, and no control or invisible character anywhere: the categories
    in FORBIDDEN_NAME_CATEGORIES, every Default_Ignorable_Code_Point and U+2800.

    The name is only a label for the admin: the key is identified by its number (`key=N` in the
    logs, the number `revoke` takes), and names need not be unique. Invisible characters are
    refused so the label shows what it holds. The errors never repeat the name, and this frame
    drops it before raising.
    """
    problem = _name_problem(name)
    if problem is not None:
        del name  # the traceback keeps this frame: it must not keep the name
        raise problem


def check_scopes(scopes: object) -> None:
    if type(scopes) is not frozenset or not scopes:
        raise ValueError("a key needs a non-empty frozenset of scopes")
    if not all(type(scope) is Scope for scope in scopes):
        raise TypeError("every key scope must be a Scope")


def is_pepper_version(value: object) -> TypeGuard[int]:
    """1 to MAX_PEPPER_VERSION, a real int (True is not a version)."""
    return is_positive_int(value) and value <= MAX_PEPPER_VERSION


def check_limit(limit: object) -> None:
    if limit is not None and not (is_positive_int(limit) and limit <= MAX_LIMIT):
        raise ValueError(f"a key limit must be an integer from 1 to {MAX_LIMIT}, or None")


def _check_record(
    key_id: int,
    name: str,
    scopes: frozenset[Scope],
    created_at: datetime,
    expires_at: datetime | None,
    revoked_at: datetime | None,
    key_hash: bytes,
    pepper_version: int,
    per_minute_limit: int | None,
    per_day_limit: int | None,
) -> None:
    if not is_positive_int(key_id):
        raise ValueError("a key id must be a positive integer")
    check_name(name)
    check_scopes(scopes)
    if not is_utc(created_at):
        raise ValueError("created_at must be an aware datetime in UTC")
    for moment in (expires_at, revoked_at):
        if moment is not None and not is_utc(moment):
            raise ValueError("expires_at and revoked_at must be aware datetimes in UTC")
    if expires_at is not None and expires_at <= created_at:
        raise ValueError("expires_at must be after created_at")
    if revoked_at is not None and revoked_at < created_at:
        raise ValueError("revoked_at cannot be before created_at")
    if type(key_hash) is not bytes or len(key_hash) != HASH_BYTES:
        raise ValueError(f"a key hash must be {HASH_BYTES} bytes")
    if not is_pepper_version(pepper_version):
        raise ValueError(f"a pepper version must be an integer from 1 to {MAX_PEPPER_VERSION}")
    check_limit(per_minute_limit)
    check_limit(per_day_limit)


def _record_problem(*values: Any) -> Exception | None:
    """The first problem of a record's values, without a traceback (its frames held them)."""
    try:
        _check_record(*values)
    except (TypeError, ValueError) as error:
        return error.with_traceback(None)
    return None


class _KeyHashSlot:
    """Holds a record's hash outside its dataclass fields."""

    __slots__ = ("_key_hash",)
    _key_hash: bytes


@dataclass(frozen=True, slots=True, kw_only=True, init=False)
class KeyRecord(_KeyHashSlot):
    """A key's record. The hash is NOT a dataclass field (it lives in a slot of the base
    class, read with `.key_hash`): dataclasses.asdict, astuple and replace and a pydantic dump
    never reach it, equality never compares it, and replace() cannot build a record."""

    id: int
    name: str = field(repr=False)
    scopes: frozenset[Scope]
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    pepper_version: int
    per_minute_limit: int | None = None
    per_day_limit: int | None = None

    def __init__(
        self,
        *,
        id: int,
        name: str,
        scopes: frozenset[Scope],
        created_at: datetime,
        expires_at: datetime | None,
        revoked_at: datetime | None,
        key_hash: bytes,
        pepper_version: int,
        per_minute_limit: int | None = None,
        per_day_limit: int | None = None,
    ) -> None:
        problem = _record_problem(
            id,
            name,
            scopes,
            created_at,
            expires_at,
            revoked_at,
            key_hash,
            pepper_version,
            per_minute_limit,
            per_day_limit,
        )
        if problem is not None:
            del name, key_hash  # the traceback keeps this frame: no name, no hash
            raise problem
        values = {
            "id": id,
            "name": name,
            "scopes": scopes,
            "created_at": created_at,
            "expires_at": expires_at,
            "revoked_at": revoked_at,
            "pepper_version": pepper_version,
            "per_minute_limit": per_minute_limit,
            "per_day_limit": per_day_limit,
            "_key_hash": key_hash,
        }
        for attribute, value in values.items():
            object.__setattr__(self, attribute, value)

    @property
    def key_hash(self) -> bytes:
        """HMAC-SHA256 of the key with the pepper of `pepper_version`."""
        return self._key_hash

    # A copy or an unpickled record would skip __init__: without its hash, or never checked.
    def _refuse(self, *_: object) -> NoReturn:
        raise TypeError("KeyRecord cannot be copied or serialised")

    __reduce__ = _refuse
    __reduce_ex__ = _refuse
    __getstate__ = _refuse
    __copy__ = _refuse
    __deepcopy__ = _refuse


@dataclass(frozen=True, slots=True)
class Caller:
    """Who is calling: the key's fixed number and its scopes."""

    key_id: int
    scopes: frozenset[Scope]


@dataclass(frozen=True, slots=True)
class Verified:
    caller: Caller


@dataclass(frozen=True, slots=True)
class Refused:
    reason: RefusalReason


type VerifyResult = Verified | Refused


class CreatedKey:
    """The clear key, shown once, and its record. Never stored.

    Not a dataclass, so dataclasses.asdict, astuple and replace and pydantic cannot read the
    key; compared and hashed by identity (object's own __eq__ and __hash__), never by the key.
    Read-only, and it cannot be pickled or copied.
    """

    __slots__ = ("_key", "_record")
    _key: str
    _record: KeyRecord

    def __init__(self, key: str, record: KeyRecord) -> None:
        object.__setattr__(self, "_key", key)
        object.__setattr__(self, "_record", record)

    @property
    def key(self) -> str:
        return self._key

    @property
    def record(self) -> KeyRecord:
        return self._record

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError("CreatedKey is read-only")

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError("CreatedKey is read-only")

    def __repr__(self) -> str:
        return f"CreatedKey(record={self._record!r})"

    __str__ = __repr__

    def _refuse(self, *_: object) -> NoReturn:
        raise TypeError("CreatedKey cannot be copied or serialised")

    __reduce__ = _refuse
    __reduce_ex__ = _refuse
    __getstate__ = _refuse
    __copy__ = _refuse
    __deepcopy__ = _refuse


__all__ = [
    "BRAILLE_PATTERN_BLANK",
    "DEFAULT_IGNORABLE",
    "DEFAULT_LIFETIME",
    "FORBIDDEN_NAME_CATEGORIES",
    "HASH_BYTES",
    "MAX_LIMIT",
    "MAX_NAME_LENGTH",
    "MAX_PEPPER_VERSION",
    "Caller",
    "CreatedKey",
    "Expiry",
    "KeyRecord",
    "RefusalReason",
    "Refused",
    "Scope",
    "Verified",
    "VerifyResult",
]
