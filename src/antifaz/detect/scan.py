"""Scan a text for personal data: validators and patterns (NER comes in issue 6)."""

from antifaz.detect.overlaps import resolve
from antifaz.detect.patterns.identifiers import find_identifiers
from antifaz.detect.patterns.personal import find_patterns
from antifaz.detect.types import Span


def scan(text: str) -> list[Span]:
    """Non-overlapping spans of personal data in the text, sorted by position."""
    return resolve([*find_identifiers(text), *find_patterns(text)])
