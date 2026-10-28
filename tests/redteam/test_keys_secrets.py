"""Red team: a virtual key, its hash and the pepper never show, and the clear key is not kept
(issue 121, PR 8a, ADR-0020).

Canary values (fake, written here) are looked for, whole and in recognisable pieces, in every
repr, str, %r, %s, f-string and log line of every object of antifaz.keys, and in the
attributes of everything the store can reach after create() and verify() return. When a call
fails half-way, the frames of antifaz.keys in the traceback no longer hold the key.
"""

import base64
import gc
import logging
import types
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

import antifaz.keys
from antifaz.api.key_fragments import KeyFragments
from antifaz.keys import pepper as pepper_module
from antifaz.keys.format import MalformedKeyError, parse_key
from antifaz.keys.pepper import PepperRing
from antifaz.keys.records import Scope, Verified
from antifaz.keys.store import MemoryKeyStore

PACKAGE = Path(antifaz.keys.__file__).resolve().parent
PEPPER = b"test-pepper-canary-" + b"7" * 32
START = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
ALL_SCOPES = frozenset(Scope)


def _store(clock: object = None) -> MemoryKeyStore:
    return MemoryKeyStore(
        PepperRing({1: PEPPER}, current=1),
        clock if clock is not None else (lambda: START),  # type: ignore[arg-type]
    )


def _forbidden(key: str, key_hash: bytes) -> list[str]:
    return [
        key,
        key_hash.hex(),
        key_hash.hex().upper(),
        repr(key_hash),
        base64.b64encode(key_hash).decode(),
        base64.urlsafe_b64encode(key_hash).decode().rstrip("="),
        PEPPER.decode(),
        PEPPER.hex(),
        repr(PEPPER),
        "test-pepper-canary",
    ]


def _assert_clean(text: str, key: str, key_hash: bytes) -> None:
    for value in _forbidden(key, key_hash):
        assert value not in text
    assert not KeyFragments([key]).found_in(text)


def _renderings(value: object) -> list[str]:
    return [repr(value), str(value), f"{value}", f"{value!r}", "%r" % (value,), "%s" % (value,)]  # noqa: UP031


def test_no_object_of_the_package_shows_the_key_its_hash_or_the_pepper(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _store()
    created = store.create("canary-app", ALL_SCOPES, per_minute_limit=5)
    key, key_hash = created.key, created.record.key_hash
    verified = store.verify(key)
    refused = store.verify(key[:-1])
    revoked = store.revoke(1)
    try:
        parse_key(key[:-1])
    except MalformedKeyError as error:
        malformed = error
    objects: list[object] = [
        created,
        created.record,
        verified,
        refused,
        revoked,
        store,
        store.list(),
        store.get(1),
        PepperRing({1: PEPPER}, current=1),
        malformed,
    ]
    assert isinstance(verified, Verified)
    objects.append(verified.caller)

    logger = logging.getLogger("antifaz.test.keys")
    with caplog.at_level(logging.DEBUG):
        for value in objects:
            for text in _renderings(value):
                _assert_clean(text, key, key_hash)
            logger.debug("object %r %s", value, value)
            logger.info("in a list %s", [value])
        try:
            raise RuntimeError(created)
        except RuntimeError:
            logger.exception("failed with %r", created)

    assert caplog.records
    _assert_clean(caplog.text, key, key_hash)


def test_the_record_name_is_not_in_its_repr() -> None:
    store = _store()
    created = store.create("Secret project name", ALL_SCOPES)

    for value in (created, created.record, store.list()):
        assert "Secret project name" not in repr(value)


def _reachable(root: object) -> Iterator[object]:
    """Everything `root` refers to, transitively, except code, modules and classes."""
    seen: set[int] = set()
    pending = [root]
    skip = (types.ModuleType, type, types.FunctionType, types.CodeType, types.BuiltinFunctionType)
    while pending:
        current = pending.pop()
        if id(current) in seen or isinstance(current, skip):
            continue
        seen.add(id(current))
        yield current
        pending.extend(gc.get_referents(current))


def _texts_reachable_from(root: object) -> list[str]:
    found: list[str] = []
    for value in _reachable(root):
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, bytes | bytearray):
            found.append(bytes(value).decode("latin-1"))
    return found


def test_the_store_keeps_only_hashes_after_create_and_verify() -> None:
    store = _store()
    keys = [store.create(f"app-{n}", ALL_SCOPES).key for n in range(3)]
    for key in keys:
        assert isinstance(store.verify(key), Verified)
        store.verify(key[:-1])

    texts = _texts_reachable_from(store)

    assert texts  # the walk does reach the records (names, hashes)
    for key in keys:
        assert all(key not in text for text in texts)
        assert all(key[4:24] not in text for text in texts)


def test_records_and_callers_hold_no_clear_key() -> None:
    store = _store()
    created = store.create("app", ALL_SCOPES)
    verified = store.verify(created.key)

    for root in (created.record, verified, store.list()):
        assert all(created.key not in text for text in _texts_reachable_from(root))


def _keys_frames(error: BaseException) -> Iterator[types.FrameType]:
    """The frames of the traceback that run code of the antifaz.keys package."""
    traceback = error.__traceback__
    while traceback is not None:
        if Path(traceback.tb_frame.f_code.co_filename).resolve().parent == PACKAGE:
            yield traceback.tb_frame
        traceback = traceback.tb_next


def _holds_a_key(frame: types.FrameType) -> bool:
    for value in frame.f_locals.values():
        for text in _texts_reachable_from(value):
            try:
                parse_key(text)
            except MalformedKeyError:
                continue
            return True
    return False


def test_a_failing_clock_during_verify_leaves_no_key_in_the_traceback() -> None:
    store = _store()
    key = store.create("app", ALL_SCOPES).key

    def broken_clock() -> datetime:
        raise RuntimeError("clock broke")

    failing = MemoryKeyStore(PepperRing({1: PEPPER}, current=1), broken_clock, store.list())
    with pytest.raises(RuntimeError) as caught:
        failing.verify(key)

    frames = list(_keys_frames(caught.value))
    assert frames
    assert not any(_holds_a_key(frame) for frame in frames)
    assert key not in str(caught.value)


def test_a_failing_hash_during_verify_leaves_no_key_in_the_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    key = store.create("app", ALL_SCOPES).key

    def broken_hmac(*_: object, **__: object) -> object:
        raise RuntimeError("hash broke")

    monkeypatch.setattr(pepper_module.hmac, "new", broken_hmac)
    with pytest.raises(RuntimeError) as caught:
        store.verify(key)

    frames = list(_keys_frames(caught.value))
    assert frames
    assert not any(_holds_a_key(frame) for frame in frames)


def test_a_failing_hash_during_create_leaves_no_key_in_the_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()

    def broken_hmac(*_: object, **__: object) -> object:
        raise RuntimeError("hash broke")

    monkeypatch.setattr(pepper_module.hmac, "new", broken_hmac)
    with pytest.raises(RuntimeError) as caught:
        store.create("app", ALL_SCOPES)

    frames = list(_keys_frames(caught.value))
    assert frames
    assert not any(_holds_a_key(frame) for frame in frames)
    assert store.list() == ()


def test_a_malformed_key_error_carries_nothing_of_the_text() -> None:
    store = _store()
    key = store.create("app", ALL_SCOPES).key

    with pytest.raises(MalformedKeyError) as caught:
        parse_key(key + "x")

    assert caught.value.args == ("malformed key",)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert not any(_holds_a_key(frame) for frame in _keys_frames(caught.value))
    for frame in _keys_frames(caught.value):
        assert all(value != key + "x" for value in frame.f_locals.values())
