"""Policy: decides what to do with each type of data.

Actions in this version: mask or allow (surrogate, block and route_local come later).
Policies are versioned; the version goes into every evidence record.

Forbidden: Never decide without a policy version. Never default to allow on detector errors.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from antifaz.detect.types import EntityType


class Action(StrEnum):
    MASK = "mask"
    ALLOW = "allow"


@dataclass(frozen=True, slots=True)
class Policy:
    """What to do per entity type. A type missing from `entities` is masked (fail safe)."""

    version: str = "builtin-1"
    entities: Mapping[EntityType, Action] = field(default_factory=lambda: MappingProxyType({}))
    on_detector_error: Literal["block"] = "block"

    def action_for(self, entity: EntityType) -> Action:
        return self.entities.get(entity, Action.MASK)


DEFAULT_POLICY = Policy(
    entities=MappingProxyType(
        {
            entity: Action.ALLOW if entity is EntityType.ES_CIF else Action.MASK
            for entity in EntityType
        }
    )
)
"""Masks every type except company CIFs, which are usually public (spec 4.2)."""
