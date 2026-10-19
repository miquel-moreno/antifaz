"""Text sizes in the served CSS follow the browser's text size (WCAG 1.4.4), 11c-3a review.

A `font-size` in px does not grow when a person makes the text bigger (the browser setting, or
the root at 200 %): the view titles, their descriptions and the «Antifaz» wordmark stayed put in
Miquel's 200 % captures. So no `font-size` (nor `font` shorthand) and no `--text-*` token of a
served stylesheet may hold a px length. The one px allowed is inside `(1rem - 16px)`: zero at
the default 16 px root and growing with it, so a size that also follows the window (cqi) still
grows with the text (the view title). The browser test tests/browser/test_panel_text_size.py
checks the result in real browsers.
"""

import re

import pytest

from antifaz.web.assets import STATIC_DIR

SHEETS = ("panel.css", "tokens.css", "fonts.css")
COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
TEXT_DECLARATION = re.compile(r"(?<![\w-])(font-size|font|--text-[a-z0-9-]+)\s*:\s*([^;}]*)")
ALLOWED_PX = re.compile(r"\(\s*1rem\s*-\s*16px\s*\)")
PX = re.compile(r"(?<![\w.])\d*\.?\d+px\b")


def px_text_sizes(css: str) -> list[str]:
    """Each text-size declaration with a px length outside `(1rem - 16px)`."""
    found = []
    for name, value in TEXT_DECLARATION.findall(COMMENT.sub("", css)):
        if PX.search(ALLOWED_PX.sub("", value)):
            found.append(f"{name}: {value.strip()}")
    return found


@pytest.mark.parametrize("sheet", SHEETS)
def test_no_text_size_in_px(sheet: str) -> None:
    assert px_text_sizes((STATIC_DIR / sheet).read_text("utf-8")) == []


def test_the_check_sees_px_text_sizes() -> None:
    css = (
        ".a{font-size:14px}.b{font-size:.875rem}:root{--text-x: 26px; --text-y: 1.5rem}"
        ".c{font:600 12px/1.4 sans-serif}.d{font-size:clamp(30px,4.4cqi,50px)}"
        "/* font-size:9px */.e{width:12px}"
        ":root{--text-t: clamp(1.875rem, calc(4.4cqi + (1rem - 16px) * 3.125), 3.125rem)}"
    )
    assert px_text_sizes(css) == [
        "font-size: 14px",
        "--text-x: 26px",
        "font: 600 12px/1.4 sans-serif",
        "font-size: clamp(30px,4.4cqi,50px)",
    ]
