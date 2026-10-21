"""Policy defaults and the placeholder table (vault) never leaking its values."""

import copy
import pickle

import pytest

from antifaz import DEFAULT_POLICY, Action, Policy, Vault
from antifaz.detect.types import EntityType
from tests.conftest import SENTINEL_DNI


def test_default_policy_masks_every_type_except_cif() -> None:
    for entity in EntityType:
        expected = Action.ALLOW if entity is EntityType.ES_CIF else Action.MASK
        assert DEFAULT_POLICY.action_for(entity) is expected


def test_default_policy_has_a_version_and_blocks_on_detector_error() -> None:
    assert DEFAULT_POLICY.version == "builtin-1"
    assert DEFAULT_POLICY.on_detector_error == "block"


def test_type_missing_from_policy_is_masked() -> None:
    assert Policy(entities={}).action_for(EntityType.ES_DNI) is Action.MASK


def _vault_with_sentinel() -> Vault:
    vault = Vault()
    vault._add(EntityType.ES_DNI, SENTINEL_DNI)
    return vault


def test_vault_repr_and_str_show_only_the_size() -> None:
    vault = _vault_with_sentinel()
    assert repr(vault) == "Vault(entries=1)"
    assert str(vault) == "Vault(entries=1)"
    assert SENTINEL_DNI not in f"{vault!r} {vault!s} {vault}"


def test_vault_same_value_same_token_and_per_type_numbering() -> None:
    vault = Vault()
    assert vault._add(EntityType.ES_DNI, "12345678Z") == "ES_DNI_1"
    assert vault._add(EntityType.EMAIL, "ana@example.com") == "EMAIL_1"
    assert vault._add(EntityType.ES_DNI, "87654321X") == "ES_DNI_2"
    assert vault._add(EntityType.ES_DNI, "12345678Z") == "ES_DNI_1"
    assert len(vault) == 3
    assert vault._lookup("ES_DNI_2") == "87654321X"
    assert vault._lookup("ES_DNI_9") is None
    assert set(vault._hidden_values()) == {
        (EntityType.ES_DNI, "12345678Z"),
        (EntityType.ES_DNI, "87654321X"),
        (EntityType.EMAIL, "ana@example.com"),
    }


def test_vault_cannot_be_pickled_copied_or_deep_copied() -> None:
    vault = _vault_with_sentinel()
    with pytest.raises(TypeError):
        pickle.dumps(vault)
    with pytest.raises(TypeError):
        copy.copy(vault)
    with pytest.raises(TypeError):
        copy.deepcopy(vault)


def test_vault_is_not_iterable_and_has_no_value_equality() -> None:
    vault = _vault_with_sentinel()
    with pytest.raises(TypeError):
        iter(vault)  # type: ignore[call-overload]
    assert vault != _vault_with_sentinel()


def test_vault_type_counts_are_distinct_values_per_type_without_any_value() -> None:
    """«En directo» (11e-1): how many distinct values of each type were hidden, nothing more."""
    vault = Vault(reserved=frozenset({"ES_DNI_1"}))  # a reserved number does not count
    vault._add(EntityType.ES_DNI, SENTINEL_DNI)
    vault._add(EntityType.ES_DNI, SENTINEL_DNI)  # the same value: one
    vault._add(EntityType.ES_DNI, "87654321X")
    vault._add(EntityType.EMAIL, "ana@example.com")

    counts = vault.type_counts()
    assert dict(counts) == {EntityType.ES_DNI: 2, EntityType.EMAIL: 1}
    assert all(type(count) is int for count in counts.values())
    assert SENTINEL_DNI not in repr(counts)
    with pytest.raises(TypeError):
        counts[EntityType.IBAN] = 1  # type: ignore[index]
    assert dict(Vault().type_counts()) == {}
