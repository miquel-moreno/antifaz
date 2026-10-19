"""Panel sessions with a real logout (ADR-0018). Standard library only.

The browser holds a random id (`secrets.token_urlsafe(32)`, 256 bits) in its cookie. The server
keeps only the SHA-256 fingerprint of that id, with the scheme it was made over, the creation
time, the last use and the CSRF token: a copy of the store does not hand out a usable cookie. A
session lasts 8 hours at most and 30 minutes without use. Expiry is checked when a session is
looked up (no timers, no thread).

- Looking up and counting a use are two steps: peek() finds a live session of the right scheme
  without touching it, and touch() counts the use. The door touches only after every check of
  the request passed, and only for requests of the panel's own page (door.py), so a refused
  request or a page of another localhost port never keeps a session alive. get() is both.

- The scheme: "https" or "http-local" (http://localhost). A session is only valid over the scheme
  it was made over. The cookie names already keep the two apart; this covers an id copied by hand
  from one cookie to the other. Presented over the other scheme, the session is CLOSED (reason
  "scheme_mismatch"), not only refused: whoever holds that id is not following the rules.
- seconds_left() (11d-b) says how long a live session has before each limit, without counting
  a use, for the panel's expiry warning (POST /panel/api/session and the panel page).
- The CSRF token is its own random value (`secrets.token_urlsafe(32)`): neither the id nor
  anything computed from it. It only ever travels in the <meta> of the panel page (no-store).

Whoever holds a live view of a session (the SSE stream of "En directo", in 11e) subscribes with
watch() (or on_close() for every session) and is told, with the reason, when that session is
closed: logout, replaced (a new login), expired, evicted or scheme_mismatch. watch() on a session
that is not there any more (closed, or never was) is told at once with "gone".

Subscribers: every one is told, whatever the others do. One that raises an Exception is logged
(a fixed message and the error's type only) and closing goes on: it never fails because of one.
One that raises a BaseException that is not an Exception (asyncio.CancelledError,
KeyboardInterrupt...) does not stop the others either: the session is already gone, every
subscriber is told, and then the FIRST such error is raised again from the call that closed it.

The store is bounded: when it is full, the OLDEST session (by creation) is evicted, its
subscribers told, and the new one is ALWAYS created. A full store never refuses a login.

Concurrency: no lock. It is correct only while every call runs on the same event loop thread
and nothing awaits in the middle of a call, so the panel routes that use it must be `async def`
(a plain `def` route would run in a thread pool). on_close callbacks are synchronous and must not
block: the SSE side only signals (asyncio.Event.set or loop.call_soon) and returns.

Forbidden: keeping the raw session id in any attribute. Logging an id, a fingerprint or a CSRF
token. Calling is_active(), touch(), seconds_left() or watch() with a value the client sent
(they take a fingerprint, which only the server computes: a client value would skip the
id-to-fingerprint step). Deriving the CSRF token from the session id.
"""

import dataclasses
import hashlib
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

type SessionScheme = Literal["https", "http-local"]
# gone: only for watch(), when the session was already closed (or never existed).
type CloseReason = Literal["logout", "replaced", "expired", "evicted", "scheme_mismatch", "gone"]
type CloseCallback = Callable[[str, CloseReason], None]

SCHEMES: frozenset[str] = frozenset({"https", "http-local"})

MAX_AGE_SECONDS = 8 * 60 * 60
IDLE_SECONDS = 30 * 60
MAX_SESSIONS = 32
_ID_BYTES = 32

logger = logging.getLogger("antifaz.panel")


def fingerprint(session_id: str) -> str:
    """The SHA-256 of a session id, in hex: what the server stores instead of the id."""
    return hashlib.sha256(session_id.encode("utf-8", "surrogatepass")).hexdigest()


@dataclass(frozen=True)
class Session:
    """What the server keeps of a session. There is no field for the id itself, and its repr
    shows neither the fingerprint nor the CSRF token (a log line or a traceback must not)."""

    fingerprint: str = field(repr=False)
    scheme: SessionScheme
    created: float
    last_used: float
    csrf_token: str = field(repr=False)

    def valid_at(self, now: float) -> bool:
        """Younger than 8 hours and used in the last 30 minutes."""
        return now - self.created < MAX_AGE_SECONDS and now - self.last_used < IDLE_SECONDS


class SessionStore(Protocol):
    """Where sessions live. In memory today; PostgreSQL can take it later (ADR-0018, item 8)."""

    def create(self, scheme: SessionScheme) -> tuple[str, Session]:
        """A new session bound to `scheme`: (the id for the cookie, what the server keeps)."""
        ...

    def peek(self, session_id: str, scheme: SessionScheme) -> Session | None:
        """The live session for this id over `scheme`, WITHOUT counting a use. None if unknown
        or expired; also None if it was made over the other scheme, and then it is closed."""
        ...

    def touch(self, key: str) -> Session | None:
        """Count a use of the live session with this fingerprint; None if it is not live.
        Forbidden: calling it with a client-supplied value."""
        ...

    def get(self, session_id: str, scheme: SessionScheme) -> Session | None:
        """peek() and, if found, touch(): the live session with its last use refreshed."""
        ...

    def delete(self, session_id: str, reason: CloseReason) -> bool:
        """Close the live session now with `reason` and tell the subscribers; True if it was
        live. An expired one is closed as "expired" instead, and that returns False."""
        ...

    def is_active(self, key: str) -> bool:
        """Whether the session with this fingerprint is still live, without counting a use.
        Forbidden: calling it with a client-supplied value."""
        ...

    def seconds_left(self, key: str) -> tuple[float, float] | None:
        """(seconds before the inactivity limit, seconds before the 8 hours) of the live session
        with this fingerprint, on the store's own clock and WITHOUT counting a use; None if it
        is not live. Raw values: near the end the first may pass the second (the caller caps).
        Forbidden: calling it with a client-supplied value."""
        ...

    def on_close(self, callback: CloseCallback) -> Callable[[], None]:
        """Call `callback(fingerprint, reason)` whenever a session closes. Returns unsubscribe."""
        ...


class MemorySessionStore:
    """SessionStore in this process's memory: every session goes when the process restarts."""

    def __init__(
        self, clock: Callable[[], float] = time.monotonic, max_sessions: int = MAX_SESSIONS
    ) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        self._clock = clock
        self._max_sessions = max_sessions
        self._sessions: dict[str, Session] = {}  # fingerprint -> session
        self._callbacks: list[CloseCallback] = []

    def __len__(self) -> int:
        return len(self._sessions)

    def create(self, scheme: SessionScheme) -> tuple[str, Session]:
        if scheme not in SCHEMES:
            raise ValueError("unknown session scheme")
        now = self._clock()
        self._drop_expired(now)
        while len(self._sessions) >= self._max_sessions:
            oldest = min(self._sessions.values(), key=lambda session: session.created)
            self._close(oldest.fingerprint, "evicted")
        session_id = secrets.token_urlsafe(_ID_BYTES)
        session = Session(
            fingerprint=fingerprint(session_id),
            scheme=scheme,
            created=now,
            last_used=now,
            csrf_token=secrets.token_urlsafe(_ID_BYTES),  # its own draw: never from the id
        )
        self._sessions[session.fingerprint] = session
        return session_id, session

    def peek(self, session_id: str, scheme: SessionScheme) -> Session | None:
        key = fingerprint(session_id)
        session = self._live(key, self._clock())
        if session is None:
            return None
        if session.scheme != scheme:
            self._close(key, "scheme_mismatch")
            return None
        return session

    def touch(self, key: str) -> Session | None:
        now = self._clock()  # once: the check and the new last use see the same instant
        session = self._live(key, now)
        if session is None:
            return None
        session = dataclasses.replace(session, last_used=now)
        self._sessions[key] = session
        return session

    def get(self, session_id: str, scheme: SessionScheme) -> Session | None:
        session = self.peek(session_id, scheme)
        return None if session is None else self.touch(session.fingerprint)

    def delete(self, session_id: str, reason: CloseReason) -> bool:
        key = fingerprint(session_id)
        if self._live(key, self._clock()) is None:  # an expired one is closed as "expired"
            return False
        self._close(key, reason)
        return True

    def is_active(self, key: str) -> bool:
        return self._live(key, self._clock()) is not None

    def seconds_left(self, key: str) -> tuple[float, float] | None:
        now = self._clock()  # once: the check and both differences see the same instant
        session = self._live(key, now)
        if session is None:
            return None
        return IDLE_SECONDS - (now - session.last_used), MAX_AGE_SECONDS - (now - session.created)

    def on_close(self, callback: CloseCallback) -> Callable[[], None]:
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe

    def _live(self, key: str, now: float) -> Session | None:
        session = self._sessions.get(key)
        if session is None:
            return None
        if not session.valid_at(now):
            self._close(key, "expired")
            return None
        return session

    def _drop_expired(self, now: float) -> None:
        for key in [k for k, session in self._sessions.items() if not session.valid_at(now)]:
            self._close(key, "expired")

    def _close(self, key: str, reason: CloseReason) -> None:
        """Forget the session, then tell EVERY subscriber. An Exception from one is logged (type
        only); the first BaseException that is not one is raised again once all were told."""
        del self._sessions[key]
        pending: BaseException | None = None
        for callback in list(self._callbacks):
            try:
                callback(key, reason)
            except Exception as error:
                # Only the type: an exception's text could carry the fingerprint.
                logger.error("session close subscriber failed: %s", type(error).__name__)
            except BaseException as error:  # CancelledError, KeyboardInterrupt...: after all
                if pending is None:
                    pending = error
        if pending is not None:
            raise pending


def _nothing() -> None:
    """The unsubscribe of a watch that ended before it started."""


def watch(store: SessionStore, key: str, cut: Callable[[CloseReason], None]) -> Callable[[], None]:
    """Call `cut(reason)` once when the session with fingerprint `key` closes; for 11e's SSE.

    A thin layer over on_close(): other sessions closing are ignored, and the subscription ends
    by itself after the one call (a session closes only once). Returns unsubscribe, for a stream
    that ends first. `cut` runs inside the close: it must only signal and return, never block.

    It subscribes first and then checks that the session is live, so no close can slip between
    the two. If that check finds it expired, it closes it and `cut` hears "expired". If the
    session is not there at all (already closed, or never was), `cut("gone")` is called at once,
    nothing stays subscribed and the unsubscribe returned does nothing.

    Forbidden: passing a value the client sent as `key` (only the server computes fingerprints).
    """

    told = False

    def on_close(closed: str, reason: CloseReason) -> None:
        nonlocal told
        if closed == key:
            told = True
            unsubscribe()
            cut(reason)

    unsubscribe = store.on_close(on_close)
    if store.is_active(key):
        return unsubscribe
    if not told:  # not expired by the check just now: it was not there at all
        unsubscribe()
        cut("gone")
    return _nothing
