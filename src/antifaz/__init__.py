"""Antifaz: a privacy gateway for LLMs.

It pseudonymises the personal data it detects before a text reaches an LLM provider and
restores it in the answer. Library use:

    result = mask("Mi DNI es 12345678Z")   # result.text == "Mi DNI es [[ES_DNI_1]]"
    restore(answer, result.vault)
"""

from antifaz.detect.scan import scan
from antifaz.detect.types import EntityType, Span
from antifaz.errors import AntifazBlocked, DetectorFailed, EgressBlocked
from antifaz.mask import MaskResult, mask
from antifaz.policy import DEFAULT_POLICY, Action, Policy
from antifaz.restore import restore
from antifaz.vault import Vault

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_POLICY",
    "Action",
    "AntifazBlocked",
    "DetectorFailed",
    "EgressBlocked",
    "EntityType",
    "MaskResult",
    "Policy",
    "Span",
    "Vault",
    "__version__",
    "mask",
    "restore",
    "scan",
]
