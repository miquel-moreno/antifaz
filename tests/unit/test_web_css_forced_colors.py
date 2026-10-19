"""The focus ring survives forced colours (Windows high contrast), issue 53, PR 11c-4.

Under `forced-colors: active` the browser drops every `box-shadow`. The panel's focus ring is a
box-shadow, so every `:focus-visible` also needs an outline the browser can paint: the global
rule carries a transparent 2 px outline (invisible in normal mode, a system colour under forced
colours), and no rule of a served sheet that can match a focused control (a `:focus`
pseudo-class, a control's element type, `[tabindex]` or `*`) may take it away with
`outline: none`, but for the allow-list below, each entry with its reason.
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


# A selector that can match a focused control: a :focus pseudo-class, a control's element type
# or [tabindex] in its last compound (the element the rule styles), or the universal selector.
CONTROL = re.compile(
    r"(:focus\b|:focus-visible|:focus-within|(?:^|[\s>+~(])(?:a|button|input|select|textarea)"
    r"(?=$|[.:#\[\s)])|\[tabindex|^\*|[\s>+~]\*)"
)
# Rules allowed to set `outline: none`, each with its reason.
ALLOWED_NO_OUTLINE = {
    # #main (tabindex=-1) takes the focus only from the skip link; it is a landmark, not a
    # control, and the skip link itself keeps its ring.
    ".app-content:focus": "the main landmark, not a control",
}


def last_compound(selector: str) -> str:
    """The last compound of a selector (what the rule styles), without its pseudo-elements."""
    part = re.split(r"\s*[>+~]\s*|\s+", selector.strip())[-1]
    return part.split("::", 1)[0]


def focus_rules_without_outline(css: str) -> list[str]:
    """Each selector that can match a focused control and whose rule sets `outline: none` (or
    0), outside ALLOWED_NO_OUTLINE."""
    found = []
    for selectors, body in RULE.findall(COMMENT.sub("", css)):
        if not NO_OUTLINE.search(body):
            continue
        for selector in (part.strip() for part in selectors.split(",")):
            if selector in ALLOWED_NO_OUTLINE:
                continue
            if ":focus" in selector or CONTROL.search(" " + last_compound(selector)):
                found.append(selector)
    return found


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
    assert focus_rules_without_outline(".d:focus{outline:none}") == [".d:focus"]
    # The composer's textarea (a control by its element type), as it was before 11c-4.
    composer = ".composer textarea{display:block;border:0;outline:none;resize:vertical}"
    assert focus_rules_without_outline(composer) == [".composer textarea"]
    assert focus_rules_without_outline("a.x,.y button,[tabindex]{outline:0}") == [
        "a.x",
        ".y button",
        "[tabindex]",
    ]
    assert focus_rules_without_outline(".m>*{outline:none}") == [".m>*"]
    assert focus_rules_without_outline("@media (x){.e:focus-within{outline:none}}") == [
        ".e:focus-within"
    ]
    # Not controls: a class, an element whose name only starts like one, a pseudo-element.
    assert focus_rules_without_outline(".card,.area,abbr{outline:none}") == []
    assert focus_rules_without_outline(".textarea-like,.button-row{outline:none}") == []
    assert focus_rules_without_outline(".app-content:focus{outline:none}") == []  # allowed
    assert focus_rules_without_outline("textarea{outline:2px solid transparent}") == []
    assert global_ring(":focus-visible{outline:none;box-shadow:0 0 0 1px red}") == (
        "outline:none;box-shadow:0 0 0 1px red"
    )
    assert not GLOBAL_RING.search("outline:none")
    assert not FORCED_BLOCK.search("@media (prefers-contrast: more){")
