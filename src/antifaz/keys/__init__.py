"""Virtual keys (issue 121, ADR-0020): one Antifaz key per client or app, with a name, scopes,
limits and an expiry, revocable at any time.

PR 8a is the library only, used by nothing yet (no route, no CLI, no setting, no database):

- format.py: `afz_` + 43 random base62 + 6-character checksum; parse_key() refuses anything
  malformed before any lookup.
- pepper.py: PepperRing, HMAC-SHA256 with a versioned pepper kept outside the store.
- records.py: KeyRecord, Scope, Caller and the closed verify result.
- store.py: the KeyStore protocol and MemoryKeyStore (PostgreSQL comes in 8b).

Standard library only (secrets, hmac, hashlib, zlib).

Forbidden: keeping a clear key after create() or verify() return. Logging a key, its hash, its
name or a pepper (logs get the key's number and a reason from the closed list only).
"""

from antifaz.keys.format import MalformedKeyError, generate_key, parse_key
from antifaz.keys.pepper import MIN_PEPPER_BYTES, PepperRing
from antifaz.keys.records import (
    DEFAULT_LIFETIME,
    Caller,
    CreatedKey,
    Expiry,
    KeyRecord,
    RefusalReason,
    Refused,
    Scope,
    Verified,
    VerifyResult,
)
from antifaz.keys.store import KeyStore, MemoryKeyStore

__all__ = [
    "DEFAULT_LIFETIME",
    "MIN_PEPPER_BYTES",
    "Caller",
    "CreatedKey",
    "Expiry",
    "KeyRecord",
    "KeyStore",
    "MalformedKeyError",
    "MemoryKeyStore",
    "PepperRing",
    "RefusalReason",
    "Refused",
    "Scope",
    "Verified",
    "VerifyResult",
    "generate_key",
    "parse_key",
]
