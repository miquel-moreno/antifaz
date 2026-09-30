"""Command line: `antifaz scan <file>`, `antifaz mask <file>` (`-` is stdin), `antifaz verify`.

Uses the library functions directly. `scan` prints `TYPE start end` per detection and
`mask` prints the masked text. Neither prints values nor the placeholder table, and errors
are generic messages that never echo the input. `verify` (cli/verify.py) plants synthetic
data with the current configuration and checks that none reaches a fake provider.

Forbidden: No dependency on the database for scan.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from antifaz.cli.verify import verify_from_environment
from antifaz.detect.scan import scan
from antifaz.mask import mask


class _Parser(argparse.ArgumentParser):
    """Usage errors never repeat the arguments: a stray one could be a value."""

    def error(self, message: str) -> NoReturn:
        self.exit(2, f"{self.prog}: invalid arguments; see {self.prog} --help\n")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="antifaz", description="Find or mask personal data.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("scan", "print TYPE start end for each detection (never the value)"),
        ("mask", "print the text with placeholders (never the table)"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("file", help="UTF-8 text file, or - for stdin")
    commands.add_parser(
        "verify",
        help="plant synthetic data with your configuration and check that none reaches a fake "
        "provider (never calls a real one)",
    )
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
    if args.command == "verify":
        return verify_from_environment()
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
    # Bytes, so a console in another encoding (cp1252 on Windows) neither crashes nor mangles.
    sys.stdout.flush()
    sys.stdout.buffer.write(output.encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


__all__ = ["main"]
