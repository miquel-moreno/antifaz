"""Virtual keys hold their rules whatever the input (issue 121, PR 8a, ADR-0020).

- Every body of 43 base62 characters makes a key that parses; every key the store creates
  verifies to its own id.
- Any single-character change (another character, one more, one less) is refused as malformed:
  CRC32 detects every change of one byte, and any other character is outside the alphabet.
- parse_key() never raises anything but MalformedKeyError, and verify() never raises at all,
  whatever it is given.
- Any key name that check_name() accepts has no control or invisible character (Unicode
  categories Cc, Cf, Zl, Zp, Cs, Co, Cn), 1 to 64 characters and no whitespace at either end.
"""

import unicodedata
from datetime import UTC, datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from antifaz.keys.format import (
    ALPHABET,
    BODY_LENGTH,
    KEY_LENGTH,
    PREFIX,
    MalformedKeyError,
    key_from_body,
    parse_key,
)
from antifaz.keys.pepper import PepperRing
from antifaz.keys.records import RefusalReason, Refused, Scope, Verified, check_name
from antifaz.keys.store import MemoryKeyStore

# Written out here, not imported: the property checks the rule, not the constant.
FORBIDDEN = frozenset({"Cc", "Cf", "Zl", "Zp", "Cs", "Co", "Cn"})
PEPPER = b"test-pepper-" + b"0" * 32
START = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)

bodies = st.text(alphabet=ALPHABET, min_size=BODY_LENGTH, max_size=BODY_LENGTH)
names = st.text(
    alphabet=st.characters(codec="utf-8", categories=("L", "N")), min_size=1, max_size=64
)
scope_sets = st.frozensets(st.sampled_from(list(Scope)), min_size=1)
anything = st.one_of(
    st.text(),
    st.binary(),
    st.integers(),
    st.none(),
    st.lists(st.text(max_size=5), max_size=3),
    st.text(alphabet=ALPHABET + "_ -\n", min_size=KEY_LENGTH - 2, max_size=KEY_LENGTH + 2),
    bodies.map(lambda body: PREFIX + body + "000000"),
)


def _store() -> MemoryKeyStore:
    return MemoryKeyStore(PepperRing({1: PEPPER}, current=1), lambda: START)


@given(bodies)
def test_every_base62_body_makes_a_key_that_parses(body: str) -> None:
    key = key_from_body(body)

    assert parse_key(key) == key
    assert key[len(PREFIX) : len(PREFIX) + BODY_LENGTH] == body


@settings(max_examples=50)
@given(st.lists(st.tuples(names, scope_sets), min_size=1, max_size=5))
def test_every_created_key_verifies_to_its_own_id_and_scopes(
    wanted: list[tuple[str, frozenset[Scope]]],
) -> None:
    store = _store()
    created = [store.create(name, scopes) for name, scopes in wanted]

    for number, item in enumerate(created, start=1):
        result = store.verify(item.key)
        assert isinstance(result, Verified)
        assert result.caller.key_id == number
        assert result.caller.scopes == wanted[number - 1][1]


single_changes = st.tuples(
    st.integers(min_value=0, max_value=KEY_LENGTH - 1),
    st.sampled_from(["replace", "insert", "delete"]),
    st.characters(),
)


def _changed(key: str, position: int, how: str, character: str) -> str:
    if how == "insert":
        return key[:position] + character + key[position:]
    if how == "delete":
        return key[:position] + key[position + 1 :]
    if character == key[position]:
        character = "0" if character != "0" else "1"
    return key[:position] + character + key[position + 1 :]


@settings(max_examples=300)
@given(single_changes)
def test_any_single_character_change_of_a_created_key_is_refused_as_malformed(
    change: tuple[int, str, str],
) -> None:
    store = _store()
    key = store.create("app", {Scope.SCAN}).key
    changed = _changed(key, *change)

    assert changed != key
    assert store.verify(changed) == Refused(RefusalReason.MALFORMED)
    try:
        parse_key(changed)
    except MalformedKeyError:
        pass
    else:
        raise AssertionError("a changed key parsed")


@settings(max_examples=300)
@given(bodies, single_changes)
def test_any_single_character_change_of_any_key_fails_its_checksum(
    body: str, change: tuple[int, str, str]
) -> None:
    key = key_from_body(body)
    changed = _changed(key, *change)

    try:
        parse_key(changed)
    except MalformedKeyError:
        return
    raise AssertionError("a changed key parsed")


@settings(max_examples=500)
@given(anything)
def test_parse_raises_only_the_closed_error(value: object) -> None:
    try:
        result = parse_key(value)
    except MalformedKeyError as error:
        assert str(error) == "malformed key"
        assert error.__context__ is None
    else:
        assert result is value


@settings(max_examples=1000)
@given(
    st.one_of(
        st.text(max_size=70),
        st.text(alphabet=st.characters(categories=("Cc", "Cf", "Zl", "Zp", "Co", "Cn"))),
        st.lists(st.characters(), max_size=8).map("".join),
    )
)
def test_any_accepted_name_has_no_control_or_invisible_character(name: str) -> None:
    try:
        check_name(name)
    except ValueError as error:
        assert not name or name not in str(error)
        return
    assert not any(unicodedata.category(character) in FORBIDDEN for character in name)
    assert 1 <= len(name) <= 64
    assert name == name.strip()


@settings(max_examples=500)
@given(anything)
def test_verify_never_raises_and_refuses_what_it_does_not_know(value: object) -> None:
    store = _store()
    store.create("app", {Scope.SCAN})

    result = store.verify(value)

    assert isinstance(result, Refused)
    assert result.reason in {RefusalReason.MALFORMED, RefusalReason.UNKNOWN}
