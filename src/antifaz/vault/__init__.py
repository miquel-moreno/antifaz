"""Placeholder table: value <-> placeholder for the current request only (ADR-0004).

Numbered per type by first appearance over the whole conversation, so the same value gets
the same placeholder without keeping state between requests. "The same value" is the pair
(type, exact text) (ADR-0012).

Forbidden: Never written to logs, traces, errors or metrics. Never persisted in clear text.
That is why it cannot be printed, iterated, compared, pickled or copied.
"""

from typing import NoReturn

from antifaz.detect.types import EntityType


class Vault:
    __slots__ = ("_by_token", "_by_value", "_counters")

    def __init__(self) -> None:
        self._by_token: dict[str, str] = {}
        self._by_value: dict[tuple[EntityType, str], str] = {}
        self._counters: dict[EntityType, int] = {}

    def _add(self, entity: EntityType, value: str) -> str:
        """Token (without brackets) for this value, creating it on first appearance."""
        key = (entity, value)
        token = self._by_value.get(key)
        if token is None:
            number = self._counters.get(entity, 0) + 1
            self._counters[entity] = number
            token = f"{entity.value.upper()}_{number}"
            self._by_value[key] = token
            self._by_token[token] = value
        return token

    def _lookup(self, token: str) -> str | None:
        """Value of an upper-case token emitted in this request, or None."""
        return self._by_token.get(token)

    def _hidden_values(self) -> tuple[tuple[EntityType, str], ...]:
        """(type, value) pairs that were hidden, for the egress guard (issue 4b)."""
        return tuple(self._by_value)

    def __len__(self) -> int:
        return len(self._by_token)

    def __repr__(self) -> str:
        return f"Vault(entries={len(self)})"

    __str__ = __repr__

    def _refuse(self, *_: object) -> NoReturn:
        raise TypeError("Vault cannot be copied or serialised")

    __reduce__ = _refuse
    __reduce_ex__ = _refuse
    __getstate__ = _refuse
    __copy__ = _refuse
    __deepcopy__ = _refuse
