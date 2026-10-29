"""Virtual keys (issue 121, ADR-0020): one Antifaz key per client or app, with a name, scopes,
limits and an expiry, revocable at any time.

PR 8a is the library, still used by no route and no CLI command. The PostgreSQL base (settings,
schema, migrations, `antifaz db`) is antifaz.db (8b-1, ADR-0021); this package never imports
it, and the PostgreSQL key store comes in 8b-2:

- format.py: `afz_` + 43 random base62 + 6-character checksum; parse_key() refuses anything
  malformed before any lookup.
- pepper.py: PepperRing, HMAC-SHA256 with a versioned pepper kept outside the store.
- records.py: KeyRecord, Scope, Caller and the closed verify result.
- store.py: the KeyStore protocol and MemoryKeyStore (the PostgreSQL store comes in 8b-2).

Both stores refuse the same records: limits up to MAX_LIMIT and pepper versions up to
MAX_PEPPER_VERSION, what PostgreSQL's integer and smallint columns hold (8b-1, D14).

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
