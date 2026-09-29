"""Check the licenses of every runtime dependency (what ships with Antifaz).

Rule (ANTIFAZ spec 5.2): nothing non-commercial or strong copyleft (GPL/AGPL/SSPL...)
in what is distributed. Weak copyleft used unmodified (LGPL, MPL, EPL) is allowed but
must be listed in docs/licencias.md. A dependency without license metadata fails until
its license is checked by hand and added to VERIFIED_BY_HAND.

    uv run python -m scripts.check_licenses
"""

import re
import subprocess
import sys
from enum import StrEnum
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LICENSES_DOC = ROOT / "docs" / "licencias.md"

# name -> license, for packages whose metadata says nothing (checked on their repository).
VERIFIED_BY_HAND: dict[str, str] = {}

FORBIDDEN = re.compile(
    r"(?<!L)\bA?GPL|GNU (Affero )?General Public|SSPL|Server Side Public|BUSL|Business Source"
    r"|Non-?Commercial|\bCC[- ]BY[- ]NC|Commons Clause|Elastic License",
    re.IGNORECASE,
)
WEAK_COPYLEFT = re.compile(
    r"\bLGPL|Lesser General Public|\bMPL|Mozilla Public|\bEPL|Eclipse Public", re.I
)
PERMISSIVE = re.compile(
    r"\bMIT\b|Apache|\bBSD\b|\bISC\b|PSF|Python Software Foundation|Unlicense|\b0BSD\b|Zlib"
    r"|CC0|MIT-CMU|HPND",
    re.IGNORECASE,
)


class Verdict(StrEnum):
    OK = "ok"
    WEAK_COPYLEFT = "weak copyleft"
    FORBIDDEN = "forbidden"
    UNKNOWN = "unknown"


def classify(license_texts: list[str]) -> Verdict:
    """Decide from every license statement a package makes (expression, field, classifiers)."""
    text = " | ".join(t for t in license_texts if t and t.strip().upper() != "UNKNOWN")
    if not text:
        return Verdict.UNKNOWN
    if FORBIDDEN.search(text):
        return Verdict.FORBIDDEN
    if WEAK_COPYLEFT.search(text):
        return Verdict.WEAK_COPYLEFT
    if PERMISSIVE.search(text):
        return Verdict.OK
    return Verdict.UNKNOWN


def license_texts(name: str) -> list[str]:
    if name in VERIFIED_BY_HAND:
        return [VERIFIED_BY_HAND[name]]
    meta = metadata.metadata(name)
    # Some packages paste the whole license text in "License": its first line is enough.
    license_field = (meta.get("License") or "").strip().splitlines()
    texts = [meta.get("License-Expression") or "", license_field[0] if license_field else ""]
    return texts + [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]


def runtime_dependencies() -> list[tuple[str, bool]]:
    """(name, only on some platforms) of the locked runtime dependencies, from uv."""
    exported = subprocess.run(
        ["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes"],  # noqa: S607 - uv from PATH, fixed args
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found: dict[str, bool] = {}
    for line in exported.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "-")):
            name = re.split(r"[=<>!~;\[ ]", line, maxsplit=1)[0].lower()
            found[name] = ";" in line  # an environment marker, e.g. sys_platform != 'win32'
    return sorted(found.items())


def documented_packages(markdown: str) -> set[str]:
    """Package names in the first column of the tables of docs/licencias.md."""
    lines = [line.strip() for line in markdown.splitlines()]
    names = set()
    for i, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        is_separator = set(line) <= set("|-: ")
        is_header = i + 1 < len(lines) and lines[i + 1].startswith("|-")
        if not is_separator and not is_header:
            names.add(line.strip("|").split("|")[0].strip().lower())
    return names


def main() -> int:
    text = LICENSES_DOC.read_text(encoding="utf-8") if LICENSES_DOC.exists() else ""
    documented = documented_packages(text)
    problems = []
    for name, platform_specific in runtime_dependencies():
        try:
            texts = license_texts(name)
        except metadata.PackageNotFoundError:
            if not platform_specific:
                raise
            print(f"{'skipped':<14} {name:<28} not used on this platform (checked in CI)")
            continue
        verdict = classify(texts)
        shown = next((t for t in texts if t), "no license metadata")
        print(f"{verdict.value:<14} {name:<28} {shown[:70]}")
        if verdict in (Verdict.FORBIDDEN, Verdict.UNKNOWN):
            problems.append(f"{name}: {verdict.value} ({shown[:70]})")
        elif verdict is Verdict.WEAK_COPYLEFT and name not in documented:
            problems.append(f"{name}: weak copyleft, add it to docs/licencias.md")
    if problems:
        print("\nLicense check failed:\n- " + "\n- ".join(problems), file=sys.stderr)
        return 1
    print("\nLicense check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
