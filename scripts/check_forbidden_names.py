"""Pre-commit hook: no forbidden name in the staged files (the list itself is never in the repo).

    uv run python -m scripts.check_forbidden_names FILE...   # what the pre-commit hook runs

The names come from a private file outside the repository: the path in the environment variable
ANTIFAZ_FORBIDDEN_NAMES_FILE or, by default, `../_privado/forbidden-names.txt` next to this clone.
One name per line, matched case-insensitively as a substring; blank lines and lines starting with
`#` are ignored; a line `allow:<word>` marks a benign word that happens to contain a name (a known
false positive), which is then skipped wherever it appears. If the file is missing or lists no
name, the hook fails (exit 1) and says where to put the list, so a clone without it cannot commit
unchecked. Only in CI (environment variable CI equal to "true", any case), where the private list
never is, it prints a warning and passes.

What is scanned:
- the path of every file;
- text files, decoded as UTF-8 (Latin-1 if that fails), line by line;
- images, only their metadata, never the compressed pixels: PNG text and EXIF chunks (tEXt,
  zTXt, iTXt, eXIf, the ICC profile name), JPEG APPn segments (EXIF, XMP, IPTC...) and comments,
  WebP EXIF and XMP chunks, GIF comment and application extensions, read by this script itself
  (no external tool). SVG is text;
- other binary files (fonts, archives) are skipped.

The output never shows the name that matched: only `forbidden name in <file>:<line>` (or `in
metadata`), with any name in the path itself replaced by `***`. Exit code 1 if anything matched.
"""

from __future__ import annotations

import os
import re
import struct
import sys
import zlib
from collections.abc import Iterator, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_VAR = "ANTIFAZ_FORBIDDEN_NAMES_FILE"
DEFAULT_LIST = ROOT.parent / "_privado" / "forbidden-names.txt"
IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


class Names:
    """The forbidden names and the benign words that contain one."""

    def __init__(self, names: Sequence[str], allow: Sequence[str] = ()) -> None:
        self.names = [n.casefold() for n in names if n.strip()]
        self.allow = [a.casefold() for a in allow if a.strip()]
        self._names = re.compile("|".join(re.escape(n) for n in self.names)) if self.names else None
        self._allow = re.compile("|".join(re.escape(a) for a in self.allow)) if self.allow else None

    def found(self, text: str) -> bool:
        if self._names is None:
            return False
        low = text.casefold()
        if self._allow is not None:
            low = self._allow.sub(lambda m: " " * len(m.group()), low)
        return self._names.search(low) is not None

    def mask(self, text: str) -> str:
        """The text with every forbidden name replaced by *** (for printing a path)."""
        if self._names is None:
            return text
        return re.compile(self._names.pattern, re.IGNORECASE).sub("***", text)


def load_names(path: Path) -> Names | None:
    if not path.is_file():
        return None
    names: list[str] = []
    allow: list[str] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("allow:"):
            allow.append(line[6:].strip())
        else:
            names.append(line)
    found = Names(names, allow)
    return found if found.names else None


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def png_metadata(data: bytes) -> Iterator[bytes]:
    pos = 8
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"tEXt":
            yield body
        elif kind == b"zTXt":
            key, _, rest = body.partition(b"\0")
            yield key
            yield _inflate(rest[1:])
        elif kind == b"iTXt":
            key, _, rest = body.partition(b"\0")
            compressed = rest[:1] == b"\1"
            # compression flag, method, language tag \0, translated keyword \0, text
            lang, _, rest = rest[2:].partition(b"\0")
            translated, _, text = rest.partition(b"\0")
            yield key + b" " + lang + b" " + translated
            yield _inflate(text) if compressed else text
        elif kind == b"eXIf":
            yield body
        elif kind == b"iCCP":
            yield body.partition(b"\0")[0]  # the profile name; the profile itself is binary
        elif kind == b"IEND":
            break


def _inflate(data: bytes) -> bytes:
    try:
        return zlib.decompress(data)
    except zlib.error:
        return data


def jpeg_metadata(data: bytes) -> Iterator[bytes]:
    pos = 2
    while pos + 4 <= len(data) and data[pos] == 0xFF:
        marker = data[pos + 1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker in (0xD9, 0xDA):  # end of image, start of scan: the pixels follow
            break
        (length,) = struct.unpack(">H", data[pos + 2 : pos + 4])
        if 0xE0 <= marker <= 0xEF or marker == 0xFE:  # APPn (EXIF, XMP, IPTC, ICC...) and COM
            yield data[pos + 4 : pos + 2 + length]
        pos += 2 + length


def webp_metadata(data: bytes) -> Iterator[bytes]:
    pos = 12
    while pos + 8 <= len(data):
        kind, length = struct.unpack("<4sI", data[pos : pos + 8])
        if kind in (b"EXIF", b"XMP "):
            yield data[pos + 8 : pos + 8 + length]
        pos += 8 + length + (length & 1)


def gif_metadata(data: bytes) -> Iterator[bytes]:
    if len(data) < 13:
        return
    flags = data[10]
    pos = 13 + (3 * 2 ** ((flags & 7) + 1) if flags & 0x80 else 0)
    while pos < len(data):
        block = data[pos]
        if block == 0x3B:  # trailer
            break
        if block == 0x21:  # extension: label, then sub-blocks
            label = data[pos + 1]
            pos, body = _sub_blocks(data, pos + 2)
            if label in (0xFE, 0xFF):  # comment, application (XMP lives here)
                yield body
        elif block == 0x2C:  # image: descriptor, optional colour table, LZW pixels (skipped)
            flags = data[pos + 9]
            pos += 10 + (3 * 2 ** ((flags & 7) + 1) if flags & 0x80 else 0) + 1
            pos, _ = _sub_blocks(data, pos)
        else:
            break


def _sub_blocks(data: bytes, pos: int) -> tuple[int, bytes]:
    out = bytearray()
    while pos < len(data) and data[pos]:
        size = data[pos]
        out += data[pos + 1 : pos + 1 + size]
        pos += 1 + size
    return pos + 1, bytes(out)


READERS = {
    ".png": png_metadata,
    ".jpg": jpeg_metadata,
    ".jpeg": jpeg_metadata,
    ".webp": webp_metadata,
    ".gif": gif_metadata,
}


def metadata_texts(path: Path) -> Iterator[str]:
    """Every metadata string of an image, never its pixels."""
    data = path.read_bytes()
    for chunk in READERS[path.suffix.lower()](data):
        yield decode(chunk)
        if b"\0" in chunk:  # Windows XP tags and some XMP are UTF-16
            yield chunk.decode("utf-16-le", errors="ignore")


def scan_file(path: Path, names: Names) -> list[str]:
    shown = names.mask(path.as_posix())
    hits = []
    if names.found(path.as_posix()):
        hits.append(f"forbidden name in the path of {shown}")
    if not path.is_file():
        return hits
    if path.suffix.lower() in IMAGES:
        if any(names.found(text) for text in metadata_texts(path)):
            hits.append(f"forbidden name in {shown} (in metadata)")
        return hits
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        return hits  # another binary file (font, archive): nothing to read as text
    for number, line in enumerate(decode(data).splitlines(), start=1):
        if names.found(line):
            hits.append(f"forbidden name in {shown}:{number}")
    return hits


def in_ci() -> bool:
    return os.environ.get("CI", "").strip().lower() == "true"


def main(argv: Sequence[str] | None = None) -> int:
    files = list(sys.argv[1:] if argv is None else argv)
    list_file = Path(os.environ.get(ENV_VAR) or DEFAULT_LIST)
    names = load_names(list_file)
    if names is None:
        problem = f"no forbidden-names list at {list_file} (or it lists no name)"
        if in_ci():
            print(f"warning: {problem}; skipping the check in CI.", file=sys.stderr)
            return 0
        print(
            f"error: {problem}. Put the private list (one name per line) at the default path"
            f" {DEFAULT_LIST}, or set the environment variable {ENV_VAR} to its path.",
            file=sys.stderr,
        )
        return 1
    hits = [hit for name in files for hit in scan_file(Path(name), names)]
    for hit in hits:
        print(hit)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
