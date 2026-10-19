"""The focus ring survives forced colours (Windows high contrast), issue 53, PR 11c-4.

Under `forced-colors: active` the browser drops every `box-shadow`. The panel's focus ring is a
box-shadow, so every `:focus-visible` also needs an outline the browser can paint: the global
rule carries a transparent 2 px outline (invisible in normal mode, a system colour under forced
colours), and no `:focus-visible` rule of a served sheet may take it away with `outline: none`.
The sheet has its `@media (forced-colors: active)` block. The browser test
tests/browser/test_panel_forced_colors.py checks the result in real browsers.
"""

import re

import pytest

from antifaz.web.assets import STATIC_DIR

SHEETS = ("panel.css", "tokens.css", "fonts.css")
COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
NO_OUTLINE = re.compile(r"(?<![\w-])outline\s*:\s*(none|0)\b")
GLOBAL_RING = re.compile(r"(?<![\w-])outline\s*:\s*2px\s+solid\s+transparent\b")
FORCED_BLOCK = re.compile(r"@media\s*\(\s*forced-colors\s*:\s*active\s*\)\s*\{")


def focus_rules_without_outline(css: str) -> list[str]:
    """Each rule whose selector has :focus-visible and that sets `outline: none` (or 0)."""
    return [
        selector.strip()
        for selector, body in RULE.findall(COMMENT.sub("", css))
        if ":focus-visible" in selector and NO_OUTLINE.search(body)
    ]


def global_ring(css: str) -> str | None:
    """The body of the bare `:focus-visible{...}` rule."""
    for selector, body in RULE.findall(COMMENT.sub("", css)):
        if selector.strip() == ":focus-visible":
            return body
    return None


@pytest.mark.parametrize("sheet", SHEETS)
def test_no_focus_rule_removes_the_outline(sheet: str) -> None:
    assert focus_rules_without_outline((STATIC_DIR / sheet).read_text("utf-8")) == []


def test_the_global_ring_has_a_transparent_outline() -> None:
    body = global_ring((STATIC_DIR / "panel.css").read_text("utf-8"))
    assert body is not None
    assert GLOBAL_RING.search(body)
    assert "box-shadow" in body  # the normal-mode ring stays the box-shadow


def test_the_sheet_has_a_forced_colours_block() -> None:
    assert FORCED_BLOCK.search((STATIC_DIR / "panel.css").read_text("utf-8"))


def test_the_checks_see_an_offender() -> None:
    bad = ".a{color:red}\n/* :focus-visible{outline:none} */\n.b:focus-visible{outline: none;}"
    assert focus_rules_without_outline(bad) == [".b:focus-visible"]
    assert focus_rules_without_outline(".c:focus-visible{outline:0}") == [".c:focus-visible"]
    assert focus_rules_without_outline(".d:focus{outline:none}") == []
    assert global_ring(":focus-visible{outline:none;box-shadow:0 0 0 1px red}") == (
        "outline:none;box-shadow:0 0 0 1px red"
    )
    assert not GLOBAL_RING.search("outline:none")
    assert not FORCED_BLOCK.search("@media (prefers-contrast: more){")
