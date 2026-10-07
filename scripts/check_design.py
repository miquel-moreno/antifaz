# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "playwright==1.63.0",
#     "axe-playwright-python==0.1.8",
# ]
# ///
"""Final check of the panel prototype (design/prototype) in a real browser.

    make design-check          # uv run scripts/check_design.py
    uv run scripts/check_design.py --quick   # one width per group, for a fast loop

Dev tool only: it is not part of `make check` and its dependencies (Playwright and
axe-playwright-python, which bundles axe-core) live in the inline metadata above, never in
pyproject.toml or uv.lock. Playwright needs its own Chromium once:
`uv run --with playwright==1.63.0 playwright install chromium`.

It serves the repository on 127.0.0.1 (a random free port) with a small http.server that only
answers under /design/ and /docs/images/ (the prototype loads its fonts, tokens and favicon from
there; nothing else of the repo, and never .env, is reachable), opens the two pages in headless
Chromium and checks, for every width (390, 768, 1024, 1280 px and 1280 px with «Móvil»), theme
(light, dark) and motion mode (normal, system «reduce», the «Menos movimiento» toggle), the five
views in the five states:

- console: no errors or warnings, and no uncaught exceptions;
- network: every request goes to the local server (or is data:/blob:): zero third parties;
- no horizontal scroll of the page;
- no clipped text: every visible text box lies inside every ancestor that clips (overflow
  hidden/clip) and inside the viewport; text that only fits by scrolling a scroll container is
  listed apart, with the containers that are meant to scroll (the phone tab row, the YAML);
- no text cut with an ellipsis (text-overflow: ellipsis with the text longer than its box);
- motion: no animation repeats forever, the status ring (only in the header and the phone bar)
  pulses three times, and with reduced motion nothing animates at all;
- accessibility: axe-core on the five views and five states, light and dark, at 1280 and 390 px
  (and on tokens-preview.html), reported by rule and impact.

Exit code 1 if anything fails. --json PATH also writes every finding as JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import Counter, defaultdict
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from axe_playwright_python.base import AXE_FILE_PATH
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Browser, ConsoleMessage, Page, Request, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PANEL = "design/prototype/antifaz-panel.html"
TOKENS = "design/prototype/tokens-preview.html"
ALLOWED_PREFIXES = ("/design/", "/docs/images/")

WIDTHS = [(390, 844), (768, 1024), (1024, 768), (1280, 800)]
THEMES = ["light", "dark"]
MOTIONS = ["normal", "system-reduce", "toggle"]
STATES = ["empty", "data", "blocked", "error", "broken"]
VIEWS = ["playground", "live", "keys", "policies", "status"]
AXE_WIDTHS = [1280, 390]
# Scroll containers that are meant to scroll sideways (the text is reachable, not cut).
INTENDED_SCROLLERS = ("app-tablist", "yaml")


class Handler(SimpleHTTPRequestHandler):
    """Serves only the prototype folders, quietly."""

    def do_GET(self) -> None:
        if not self.path.startswith(ALLOWED_PREFIXES):
            self.send_error(403)
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        if not self.path.startswith(ALLOWED_PREFIXES):
            self.send_error(403)
            return
        super().do_HEAD()

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


# Runs in the page: everything that must hold at rest, in the current view and state.
INSPECT_JS = """
(intended) => {
  const de = document.documentElement, vw = de.clientWidth;
  const out = {hscroll: de.scrollWidth > vw + 0 || document.body.scrollWidth > vw,
               scrollWidth: de.scrollWidth, clientWidth: vw,
               clipped: [], scrolled: [], ellipsis: [], infinite: [], anims: 0};
  const name = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
    (typeof el.className === 'string' && el.className.trim() ?
     '.' + el.className.trim().split(/\\s+/).join('.') : '');
  const invisible = (el) => {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (e.classList.contains('sr-only') || cs.opacity === '0' || cs.visibility === 'hidden' ||
          cs.display === 'none') return true;
      // Visually hidden on purpose (legends, the table head of the keys as cards): 1 px boxes
      // that clip their content are for screen readers, not cut text.
      const b = e.getBoundingClientRect();
      const tiny = b.width <= 1 && b.height <= 1 && cs.overflow !== 'visible';
      if ((cs.clip && cs.clip !== 'auto') || tiny) return true;
    }
    return false;
  };
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT,
    {acceptNode: (n) => n.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT});
  const seen = new Set();
  for (let n; (n = walker.nextNode());) {
    const p = n.parentElement;
    if (!p || p.closest('script,style,textarea,.intro-overlay') || invisible(p)) continue;
    const range = document.createRange(); range.selectNodeContents(n);
    const rects = [...range.getClientRects()].filter((r) => r.width > 0.5 && r.height > 0.5);
    for (const r of rects) {
      let inScroller = null, cut = null;
      for (let a = p; a && a !== document.body && a !== de; a = a.parentElement) {
        const cs = getComputedStyle(a);
        if (cs.overflowX === 'visible' && cs.overflowY === 'visible') continue;
        const b = a.getBoundingClientRect();
        const outside = r.left < b.left - 1 || r.right > b.right + 1 ||
                        r.top < b.top - 2 || r.bottom > b.bottom + 2;
        if (!outside) continue;
        const scrolls = /auto|scroll/.test(cs.overflowX + cs.overflowY);
        // Inside a scroll container the text is reachable by scrolling it: stop there.
        if (scrolls) { inScroller = a; break; } else { cut = a; break; }
      }
      if (!cut && !inScroller && (r.left < -1 || r.right > vw + 1)) cut = de;
      const key = name(p) + '|' + n.nodeValue.trim().slice(0, 40);
      if (seen.has(key)) continue;
      if (cut) {
        seen.add(key);
        out.clipped.push({el: name(p), text: n.nodeValue.trim().slice(0, 60), by: name(cut)});
      }
      else if (inScroller) {
        seen.add(key);
        const cls = String(inScroller.className);
        out.scrolled.push({el: name(p), text: n.nodeValue.trim().slice(0, 60), in: name(inScroller),
                           intended: intended.some((c) => cls.split(/\\s+/).includes(c))});
      }
    }
  }
  for (const el of document.body.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    if (cs.textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth && !invisible(el))
      out.ellipsis.push({el: name(el), text: el.textContent.trim().slice(0, 60)});
  }
  for (const a of document.getAnimations()) {
    const t = a.effect && a.effect.getComputedTiming ? a.effect.getComputedTiming() : {};
    const target = a.effect && a.effect.target;
    if (t.iterations === Infinity) out.infinite.push(target ? name(target) : '?');
    if (a.playState === 'running' || a.playState === 'paused') out.anims++;
  }
  return out;
}
"""

FINISH_JS = """
() => { for (const a of document.getAnimations()) {
  try { a.finish(); } catch (e) { /* an infinite one cannot finish: INSPECT_JS reports it */ } } }
"""

SET_STATE_JS = """
(s) => { const r = document.getElementById('st-' + s); r.checked = true;
         r.dispatchEvent(new Event('change', {bubbles: true})); }
"""

TOGGLE_JS = """
(id) => { const c = document.getElementById(id); c.checked = true;
          c.dispatchEvent(new Event('change', {bubbles: true})); }
"""


class Run:
    """Collects console messages, requests and findings for every page."""

    def __init__(self, origin: str) -> None:
        self.origin = origin
        self.console: list[str] = []
        self.requests: Counter[str] = Counter()
        self.third_party: list[str] = []
        self.findings: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.checked = 0
        self.rings = 0
        self.axe: list[dict[str, Any]] = []

    def watch(self, page: Page, where: str) -> None:
        def on_console(msg: ConsoleMessage) -> None:
            if msg.type in ("error", "warning"):
                self.console.append(f"{where}: [{msg.type}] {msg.text}")

        def on_request(req: Request) -> None:
            url = req.url
            local = url.startswith(self.origin + "/")
            ok = local or url.startswith(("data:", "blob:", "about:"))
            self.requests["local" if local else url.split(":", 1)[0]] += 1
            if not ok:
                self.third_party.append(f"{where}: {url}")

        def on_failed(req: Request) -> None:
            self.console.append(f"{where}: request failed {req.url} ({req.failure})")

        page.on("console", on_console)
        page.on("pageerror", lambda exc: self.console.append(f"{where}: [pageerror] {exc}"))
        page.on("request", on_request)
        page.on("requestfailed", on_failed)

    def add(self, kind: str, where: str, item: Any) -> None:
        self.findings[kind].append({"where": where, "item": item})


def settle(page: Page, ms: int) -> None:
    page.wait_for_timeout(ms)
    page.evaluate(FINISH_JS)
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")


def open_page(
    browser: Browser, run: Run, url: str, width: int, height: int, theme: str, motion: str
) -> Page:
    context = browser.new_context(
        viewport={"width": width, "height": height},
        color_scheme=theme,  # type: ignore[arg-type]
        reduced_motion="reduce" if motion == "system-reduce" else "no-preference",
    )
    page = context.new_page()
    run.watch(page, f"{url.rsplit('/', 1)[-1]} {width}px {theme} {motion}")
    page.goto(url, wait_until="load")
    page.evaluate("() => document.fonts.ready.then(() => true)")
    if motion == "normal":
        # The entry plays once per session: let it finish (its own end hook removes it).
        settle(page, 300)
        page.wait_for_function("() => !document.getElementById('intro')", timeout=8000)
    return page


def inspect(page: Page, run: Run, where: str, motion: str) -> None:
    res = page.evaluate(INSPECT_JS, list(INTENDED_SCROLLERS))
    run.checked += 1
    if res["hscroll"]:
        run.add("hscroll", where, f"{res['scrollWidth']} > {res['clientWidth']}")
    for c in res["clipped"]:
        run.add("clipped", where, c)
    for s in res["scrolled"]:
        run.add("scrolled-intended" if s["intended"] else "scrolled", where, s)
    for e in res["ellipsis"]:
        run.add("ellipsis", where, e)
    for i in res["infinite"]:
        run.add("infinite", where, i)
    if motion != "normal" and res["anims"]:
        run.add("motion-in-reduce", where, res["anims"])


# Right after a state change: the status ring must (re)start, once, in the visible header or
# phone-bar status only, for three pulses (and not at all with reduced motion).
RINGS_JS = """
() => document.getAnimations().filter((a) => a.animationName === 'ring').map((a) => {
  const t = a.effect.target;
  return {header: !!(t && t.closest('.app-status')),
          iterations: a.effect.getComputedTiming().iterations,
          state: a.playState, ms: Math.round(a.currentTime || 0)};
})
"""


def check_rings(page: Page, run: Run, where: str, motion: str) -> None:
    rings = page.evaluate(RINGS_JS)
    run.rings += 1
    if motion != "normal":
        if rings:
            run.add("motion-in-reduce", where, f"status ring: {rings}")
        return
    ok = (
        len(rings) == 1
        and rings[0]["header"]
        and rings[0]["iterations"] == 3
        and rings[0]["state"] == "running"
        and rings[0]["ms"] < 1000
    )
    if not ok:
        run.add("ring", where, rings)


def check_panel(browser: Browser, run: Run, quick: bool) -> None:
    url = f"{run.origin}/{PANEL}"
    groups: list[tuple[int, int, bool]] = [(w, h, False) for w, h in WIDTHS] + [(1280, 800, True)]
    if quick:
        groups = [(390, 844, False), (1280, 800, False)]
    for width, height, phone in groups:
        for theme in THEMES:
            for motion in MOTIONS:
                label = f"{width}px{'+Móvil' if phone else ''} {theme} {motion}"
                page = open_page(browser, run, url, width, height, theme, motion)
                if motion == "system-reduce":
                    box = page.evaluate(
                        "() => { const c = document.getElementById('proto-motion');"
                        " return [c.checked, c.disabled]; }"
                    )
                    if box != [True, True]:
                        run.add("motion-in-reduce", label, f"toggle not locked on: {box}")
                if motion == "toggle":
                    page.evaluate(TOGGLE_JS, "proto-motion")
                if phone:
                    page.evaluate(TOGGLE_JS, "proto-mobile")
                wait = 450 if motion == "normal" else 120
                do_axe = (
                    not phone and width in AXE_WIDTHS and motion == "normal" and not quick
                ) or (quick and motion == "normal")
                for state in STATES:
                    page.evaluate(SET_STATE_JS, state)
                    check_rings(page, run, f"{label} · {state}", motion)
                    for view in VIEWS:
                        page.evaluate(f"() => document.getElementById('tab-{view}').click()")
                        settle(page, wait)
                        where = f"{label} · {state} · {view}"
                        inspect(page, run, where, motion)
                        if do_axe:
                            axe_page(page, run, f"panel {width}px {theme}", state, view)
                page.context.close()


AXE = Axe.from_file(AXE_FILE_PATH)
AXE_OPTIONS = {"resultTypes": ["violations", "incomplete", "passes"]}


def axe_page(page: Page, run: Run, group: str, state: str, view: str) -> None:
    result = AXE.run(page, options=AXE_OPTIONS).response
    run.axe.append(
        {
            "group": group,
            "state": state,
            "view": view,
            "violations": [
                {
                    "id": v["id"],
                    "impact": v["impact"],
                    "help": v["help"],
                    "tags": v["tags"],
                    "nodes": [n["target"] for n in v["nodes"]],
                }
                for v in result["violations"]
            ],
            "incomplete": [
                {"id": v["id"], "impact": v["impact"], "nodes": [n["target"] for n in v["nodes"]]}
                for v in result["incomplete"]
            ],
            "contrast_passed": sum(
                len(v["nodes"]) for v in result["passes"] if v["id"] == "color-contrast"
            ),
            "version": result.get("testEngine", {}).get("version"),
        }
    )


def check_tokens(browser: Browser, run: Run) -> None:
    url = f"{run.origin}/{TOKENS}"
    for width, height in [(390, 844), (1280, 800)]:
        for theme in THEMES:
            for motion in ("normal", "system-reduce"):
                page = open_page(browser, run, url, width, height, theme, motion)
                settle(page, 200)
                where = f"tokens {width}px {theme} {motion}"
                inspect(page, run, where, motion)
                if motion == "normal":
                    axe_page(page, run, f"tokens {width}px {theme}", "-", "tokens")
                page.context.close()


SEVERE = ("critical", "serious")


def report(run: Run, seconds: float) -> int:
    print(f"\nDesign check · {run.checked} view/state combinations in {seconds:.0f} s")
    print(f"Requests: {dict(run.requests)} · third parties: {len(run.third_party)}")
    for t in run.third_party[:10]:
        print("  THIRD PARTY", t)
    print(f"Console errors/warnings: {len(run.console)}")
    for c in run.console[:15]:
        print("  ", c)
    failing = 0
    labels = {
        "hscroll": "Horizontal page scroll",
        "clipped": "Clipped text",
        "ellipsis": "Text cut with an ellipsis",
        "scrolled": "Text only reachable by scrolling (unexpected container)",
        "infinite": "Animations that repeat forever",
        "ring": f"Status ring not restarted, 3 pulses, header only ({run.rings} state changes)",
        "motion-in-reduce": "Motion with reduced motion",
        "scrolled-intended": "Text in containers meant to scroll (info)",
    }
    for kind, label in labels.items():
        items = run.findings.get(kind, [])
        print(f"{label}: {len(items)}")
        first: dict[str, str] = {}
        distinct: Counter[str] = Counter()
        for i in items:
            text = json.dumps(i["item"], ensure_ascii=False)
            distinct[text] += 1
            first.setdefault(text, i["where"])
        for text, n in distinct.most_common(8):
            print(f"   {n:>4} x {text}  (e.g. {first[text]})")
        if kind != "scrolled-intended":
            failing += len(items)
    failing += len(run.third_party) + len(run.console)

    print(f"\naxe-core {run.axe[0]['version'] if run.axe else '?'}: {len(run.axe)} runs")
    by_rule: dict[tuple[str, str], set[str]] = defaultdict(set)
    per_group: dict[str, Counter[str]] = defaultdict(Counter)
    incomplete: Counter[str] = Counter()
    odd_incomplete: dict[str, str] = {}
    for r in run.axe:
        key = f"{r['group']} · {r['view']}"
        for v in r["violations"]:
            by_rule[(v["id"], v["impact"])].add(f"{key} · {r['state']}")
            per_group[key][v["impact"]] += 1
            if v["impact"] in SEVERE:
                failing += 1
        for i in r["incomplete"]:
            incomplete[i["id"]] += len(i["nodes"])
            if i["id"] != "color-contrast":
                odd_incomplete.setdefault(i["id"], f"{key} · {r['state']}: {i['nodes'][:4]}")
    if not by_rule:
        print("  No violations.")
    for (rule, impact), where in sorted(by_rule.items(), key=lambda kv: kv[0][1] or ""):
        print(f"  {impact:<9} {rule}: {len(where)} runs (e.g. {sorted(where)[0]})")
    groups = sorted({f"{r['group']} · {r['view']}" for r in run.axe})
    for g in groups:
        c = per_group.get(g, Counter())
        print(f"  {g}: " + (", ".join(f"{k} {n}" for k, n in c.items()) or "0 violations"))
    passed = sum(r["contrast_passed"] for r in run.axe)
    print(f"  color-contrast: {passed} text nodes pass")
    if incomplete:
        review = ", ".join(f"{k} x{n}" for k, n in incomplete.items())
        print(f"  Needs review (incomplete, nodes): {review}")
        for rule, example in odd_incomplete.items():
            print(f"    {rule}: {example}")
    print("\nRESULT:", "OK" if failing == 0 else f"{failing} problems")
    return 0 if failing == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--quick", action="store_true", help="390 and 1280 px only")
    parser.add_argument("--json", type=Path, help="also write every finding to this file")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    server, origin = serve()
    run = Run(origin)
    start = time.monotonic()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                check_panel(browser, run, args.quick)
                check_tokens(browser, run)
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
    code = report(run, time.monotonic() - start)
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "findings": run.findings,
                    "console": run.console,
                    "third_party": run.third_party,
                    "requests": run.requests,
                    "axe": run.axe,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
    return code


if __name__ == "__main__":
    sys.exit(main())
