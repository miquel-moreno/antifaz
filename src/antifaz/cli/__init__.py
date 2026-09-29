"""Command line: `antifaz scan <file>` and `antifaz mask <file>` (`-` reads stdin).

Uses the library functions directly. `scan` prints `TYPE start end` per detection and
`mask` prints the masked text. Neither prints values nor the placeholder table, and errors
are generic messages that never echo the input.

Forbidden: No dependency on the database for scan.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from antifaz.detect.scan import scan
from antifaz.mask import mask


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="antifaz", description="Find or mask personal data.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("scan", "print TYPE start end for each detection (never the value)"),
        ("mask", "print the text with placeholders (never the table)"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("file", help="UTF-8 text file, or - for stdin")
    return parser


def _read(name: str) -> str | None:
    try:
        if name == "-":
            return sys.stdin.buffer.read().decode("utf-8")
        return Path(name).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    text = _read(args.file)
    if text is None:
        print("antifaz: cannot read the input as UTF-8 text", file=sys.stderr)
        return 2
    failed = False
    try:
        if args.command == "scan":
            output = "".join(f"{s.type} {s.start} {s.end}\n" for s in scan(text))
        else:
            output = mask(text, detector=scan).text
    except Exception:  # the error may hold the text: never shown
        failed = True
    if failed:
        print("antifaz: personal data detector failed", file=sys.stderr)
        return 2
    sys.stdout.write(output)
    return 0


__all__ = ["main"]
