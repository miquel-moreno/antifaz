"""Record the terminal demo of the README (docs/images/demo.gif) from real command output.

    make demo      # uv run --with pillow==<pinned> python -m scripts.demo_gif

What it does, with synthetic data only (scripts/demo/ticket.txt):

1. Starts the fake provider of the end-to-end tests (tests/e2e/fake_upstream.py) on
   127.0.0.1:9000: it records the bytes it gets and echoes the user text.
2. Starts Antifaz (`uvicorn --factory antifaz.api.app:create_app`) on 127.0.0.1:8000 with
   throwaway random keys, in an empty temporary folder: your .env is never read, and no
   real provider is ever called.
3. Runs the commands shown in the GIF (`cat`, `antifaz scan`, `antifaz mask`, the official
   OpenAI SDK in scripts/demo/ask.py, scripts/demo/provider_received.py) and captures their
   real output. It stops if a synthetic value reached the fake provider or did not come back.
4. Draws the session as terminal frames with Pillow (no browser) and writes the GIF.

`--text` only prints the captured session (no Pillow needed). Pillow is not a project
dependency: `make demo` brings it for this run only.
"""

import argparse
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path

from tests.e2e.fake_upstream import Handler

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "scripts" / "demo"
OUTPUT = ROOT / "docs" / "images" / "demo.gif"
GATEWAY_PORT = 8000
FAKE_PORT = 9000
# The synthetic values of ticket.txt: they must never reach the fake provider.
VALUES = ("12345678Z", "ana.prueba@example.com", "ES9121000418450200051332")


@dataclass(frozen=True)
class Step:
    comments: tuple[str, ...]
    shown: str  # the command as it appears in the GIF
    argv: tuple[str, ...]  # what really runs (same command, with this venv's executables)


def _venv_script(name: str) -> str:
    folder = Path(sys.executable).parent
    for candidate in (folder / f"{name}.exe", folder / name):
        if candidate.exists():
            return str(candidate)
    raise SystemExit(f"demo: {name} not found next to {sys.executable} (run `uv sync`)")


def _cat(name: str) -> tuple[str, ...]:
    """`cat`, or the same bytes through Python where there is no `cat` (plain Windows)."""
    cat = shutil.which("cat")
    if cat:
        return (cat, name)
    code = f"import sys; sys.stdout.buffer.write(open({name!r}, 'rb').read())"
    return (sys.executable, "-c", code)


def steps() -> list[Step]:
    python = sys.executable
    return [
        Step(
            ("# A support ticket (synthetic data)",),
            "cat ticket.txt",
            _cat("ticket.txt"),
        ),
        Step(
            ("# 1. Where is the personal data? Types and positions, never the values",),
            "antifaz scan ticket.txt",
            (_venv_script("antifaz"), "scan", "ticket.txt"),
        ),
        Step(
            ("# 2. The same text with placeholders",),
            "antifaz mask ticket.txt",
            (_venv_script("antifaz"), "mask", "ticket.txt"),
        ),
        Step(
            (
                "# 3. Through the gateway: the official OpenAI SDK pointed at",
                "#    http://localhost:8000/v1 (behind it, a fake provider echoes what it gets)",
            ),
            "python ask.py",
            (python, "ask.py"),
        ),
        Step(
            ("# What the provider received:",),
            "python provider_received.py",
            (python, "provider_received.py"),
        ),
    ]


def _port_free(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _clean_env() -> dict[str, str]:
    """This environment without Antifaz, provider, SDK or proxy settings, UTF-8 output."""
    dropped = ("ANTIFAZ_", "OPENAI_", "ANTHROPIC_")
    proxies = {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(dropped) and key.lower() not in proxies
    }
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


@contextmanager
def running_stack(gateway_key: str) -> Iterator[None]:
    for port in (GATEWAY_PORT, FAKE_PORT):
        if not _port_free(port):
            raise SystemExit(f"demo: port {port} is busy (the demo needs 8000 and 9000)")
    fake = ThreadingHTTPServer(("127.0.0.1", FAKE_PORT), Handler)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    env = _clean_env() | {
        "ANTIFAZ_API_KEY": gateway_key,
        "ANTIFAZ_OPENAI_API_KEY": secrets.token_hex(24),
        "ANTIFAZ_OPENAI_BASE_URL": f"http://127.0.0.1:{FAKE_PORT}/v1",
        "ANTIFAZ_ALLOWED_HOSTS": "localhost,127.0.0.1",
        "ANTIFAZ_NER_ENABLED": "false",
        "ANTIFAZ_LOG_LEVEL": "WARNING",
    }
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "--factory",
        "antifaz.api.app:create_app",
        "--host",
        "127.0.0.1",
        "--port",
        str(GATEWAY_PORT),
        "--no-access-log",
    ]
    with tempfile.TemporaryDirectory() as empty:  # no .env here: yours is never read
        server = subprocess.Popen(  # noqa: S603 (fixed argument list, no shell)
            command, cwd=empty, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            _wait_healthy()
            yield
        finally:
            server.terminate()
            server.wait(timeout=20)
            fake.shutdown()
            fake.server_close()


def _wait_healthy() -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{GATEWAY_PORT}/healthz", headers={"Host": "localhost"}
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=2) as answer:  # noqa: S310
                if answer.status == 200:
                    return
        except OSError:
            time.sleep(0.3)
    raise SystemExit("demo: Antifaz did not start (check `make dev` with a valid .env)")


def capture() -> list[tuple[Step, str]]:
    """Run every step for real and return its output. Fails on any leak or missing value."""
    gateway_key = secrets.token_hex(32)
    session: list[tuple[Step, str]] = []
    with running_stack(gateway_key):
        env = _clean_env() | {"ANTIFAZ_API_KEY": gateway_key}
        for step in steps():
            done = subprocess.run(  # noqa: S603 (fixed argument list, no shell)
                step.argv, cwd=DEMO, env=env, capture_output=True, timeout=60, check=False
            )
            if done.returncode != 0:
                raise SystemExit(f"demo: `{step.shown}` failed with code {done.returncode}")
            session.append((step, done.stdout.decode("utf-8").replace("\r\n", "\n")))
    outputs = {step.shown: text for step, text in session}
    for value in VALUES:
        if value in outputs["python provider_received.py"]:
            raise SystemExit("demo: a synthetic value reached the fake provider")
        if value in outputs["antifaz mask ticket.txt"]:
            raise SystemExit("demo: a synthetic value was not masked")
        if value not in outputs["python ask.py"]:
            raise SystemExit("demo: a synthetic value did not come back")
    if gateway_key in "".join(outputs.values()):
        raise SystemExit("demo: the gateway key appeared in the output")
    return session


# ---------------------------------------------------------------- drawing (Pillow) ----------

COLUMNS = 92
ROWS = 24
FONT_SIZE = 16
PAD = 20
BAR = 34
BG = (24, 26, 33)
FG = (220, 223, 228)
DIM = (128, 136, 150)
PROMPT = (126, 200, 138)
PLACEHOLDER = (240, 190, 90)
VALUE = (130, 180, 245)
DOTS = ((237, 106, 94), (245, 191, 79), (98, 197, 84))
FONTS = (
    "C:/Windows/Fonts/consola.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
)


@dataclass
class Line:
    text: str
    kind: str  # "comment", "command" or "output"


def _wrap(text: str) -> list[str]:
    """Soft-wrap like a terminal: the text is never changed, only cut at COLUMNS."""
    return [text[i : i + COLUMNS] for i in range(0, len(text), COLUMNS)] or [""]


def _segments(text: str, kind: str) -> list[tuple[str, tuple[int, int, int]]]:
    """Colour runs of one line: placeholders amber, synthetic values blue."""
    if kind == "comment":
        return [(text, DIM)]
    if kind == "command":
        return [("$ ", PROMPT), (text[2:], FG)]
    marks: list[tuple[int, int, tuple[int, int, int]]] = []
    start = text.find("[[")
    while start != -1:
        end = text.find("]]", start)
        if end == -1:
            break
        marks.append((start, end + 2, PLACEHOLDER))
        start = text.find("[[", end)
    for value in VALUES:
        at = text.find(value)
        if at != -1:
            marks.append((at, at + len(value), VALUE))
    marks.sort()
    runs: list[tuple[str, tuple[int, int, int]]] = []
    position = 0
    for begin, finish, colour in marks:
        runs += [(text[position:begin], FG), (text[begin:finish], colour)]
        position = finish
    return [*runs, (text[position:], FG)]


def render(session: list[tuple[Step, str]], output: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    font_path = next((path for path in FONTS if Path(path).exists()), None)
    if font_path is None:
        raise SystemExit("demo: no monospaced font found (Consolas, DejaVu Sans Mono or Menlo)")
    font = ImageFont.truetype(font_path, FONT_SIZE)
    cell = font.getbbox("M")[2]
    line_height = FONT_SIZE + 6
    width = PAD * 2 + cell * COLUMNS
    height = BAR + PAD * 2 + line_height * ROWS

    # A fixed palette: background, and 8 steps from the background to each colour, so the
    # anti-aliased text keeps smooth edges without dithering noise.
    colours = [FG, DIM, PROMPT, PLACEHOLDER, VALUE, *DOTS, (60, 63, 74)]
    palette = [BG]
    for colour in colours:
        palette += [
            tuple(round(b + (c - b) * step / 8) for b, c in zip(BG, colour, strict=True))
            for step in range(1, 9)
        ]
    flat = [channel for colour in palette for channel in colour]
    palette_image = Image.new("P", (1, 1))
    palette_image.putpalette(flat + [0] * (768 - len(flat)))

    def frame(lines: list[Line], cursor: bool) -> Image.Image:
        image = Image.new("RGB", (width, height), BG)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, width, BAR), fill=(36, 39, 48))
        for index, colour in enumerate(DOTS):
            x = PAD + index * 20
            draw.ellipse((x, BAR // 2 - 6, x + 12, BAR // 2 + 6), fill=colour)
        title = "antifaz · synthetic data only"
        draw.text(((width - font.getlength(title)) / 2, 8), title, font=font, fill=DIM)
        rows: list[tuple[str, str]] = []
        for line in lines:
            rows += [(piece, line.kind) for piece in _wrap(line.text)]
        rows = rows[-ROWS:]
        y = BAR + PAD
        for text, kind in rows:
            x = float(PAD)
            for run, colour in _segments(text, kind):
                draw.text((x, y), run, font=font, fill=colour)
                x += font.getlength(run)
            y += line_height
        if cursor and rows:
            last, kind = rows[-1]
            x = PAD + font.getlength(last)
            draw.rectangle((x + 1, y - line_height + 2, x + cell - 1, y - 4), fill=FG)
        return image.quantize(palette=palette_image, dither=Image.Dither.NONE)

    frames: list[Image.Image] = []
    durations: list[int] = []
    shown: list[Line] = []

    def hold(milliseconds: int, cursor: bool = False) -> None:
        frames.append(frame(shown, cursor))
        durations.append(milliseconds)

    hold(700, cursor=True)
    for number, (step, text) in enumerate(session):
        if number:
            shown.append(Line("", "output"))
        for comment in step.comments:
            shown.append(Line(comment, "comment"))
        shown.append(Line("$ ", "command"))
        hold(500, cursor=True)
        for end in range(3, len(step.shown) + 3, 3):  # typed three letters at a time
            shown[-1] = Line("$ " + step.shown[:end], "command")
            hold(45, cursor=True)
        hold(350, cursor=True)
        shown.extend(Line(row, "output") for row in text.rstrip("\n").split("\n"))
        hold(2600 if number >= 2 else 1900)
    hold(4000)
    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=1,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--text", action="store_true", help="only print the captured session")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    session = capture()
    for step, text in session:
        print("\n".join(step.comments))
        print(f"$ {step.shown}")
        print(text, end="")
    if not args.text:
        render(session, OUTPUT)
        print(f"\n{OUTPUT.relative_to(ROOT)}: {OUTPUT.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
