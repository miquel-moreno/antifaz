"""What a served panel page may hold (ADR-0018: CSP with default-src 'none', nothing inline).

Parsed with html.parser, not with a regular expression: no <style>, no <script> without src (and
no code inside one), no style="..." or on...= attribute, no <base>, frames, objects or meta
refresh, and every URL attribute either a static file of the manifest, a fragment ("#...") or a
fixed panel path. Never javascript: or data:.
"""

from html.parser import HTMLParser

FIXED_PANEL_PATHS = frozenset({"/panel", "/panel/login"})
URL_ATTRIBUTES = frozenset(
    {"href", "src", "srcset", "action", "formaction", "poster", "background", "xlink:href", "data"}
)
FORBIDDEN_TAGS = frozenset({"style", "base", "iframe", "frame", "object", "embed", "applet"})


class _Audit(HTMLParser):
    def __init__(self, static_paths: frozenset[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.static_paths = static_paths
        self.problems: list[str] = []
        self.urls: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        if tag in FORBIDDEN_TAGS:
            self.problems.append(f"<{tag}>")
        if tag == "script":
            self._in_script = True
            if not values.get("src"):
                self.problems.append("<script> without src")
        if tag == "meta" and values.get("http-equiv", "").lower() == "refresh":
            self.problems.append("meta refresh")
        for name, value in values.items():
            if name == "style":
                self.problems.append(f"style= on <{tag}>")
            if name.startswith("on"):
                self.problems.append(f"{name}= on <{tag}>")
            if name in URL_ATTRIBUTES:
                self._check_url(tag, name, value)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag == "script":
            self._in_script = False

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script and data.strip():
            self.problems.append("code inside <script>")

    def _check_url(self, tag: str, name: str, value: str) -> None:
        url = value.strip()
        self.urls.append(url)
        lowered = "".join(url.split()).lower()
        if lowered.startswith(("javascript:", "data:", "vbscript:")):
            self.problems.append(f"{name}={url!r} on <{tag}>")
        elif url.startswith("/panel/static/"):
            if url not in self.static_paths:
                self.problems.append(f"{name}={url!r} is not in the manifest")
        elif not (url.startswith("#") or url in FIXED_PANEL_PATHS):
            self.problems.append(f"{name}={url!r} on <{tag}> is not a panel URL")


def html_problems(page: str, static_paths: frozenset[str]) -> list[str]:
    """Everything in `page` the panel's CSP or its URL rule would not allow (empty if clean)."""
    audit = _Audit(static_paths)
    audit.feed(page)
    audit.close()
    return audit.problems


def html_urls(page: str) -> list[str]:
    """Every URL attribute of `page`, in order."""
    audit = _Audit(frozenset())
    audit.feed(page)
    audit.close()
    return audit.urls
