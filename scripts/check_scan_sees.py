"""Fail the CI if the scanners go blind on the image (issue 54, ADR-0019).

On the distroless image the Debian packages are visible only through their records in
/var/lib/dpkg/status.d, and CPython only through its binary. If a record went missing (or a
library were copied without one), Syft would list fewer packages, Grype would report fewer
vulnerabilities, and the gate would pass for the wrong reason. So this checks three canaries:

- the SBOM (Syft, CycloneDX JSON) lists the Debian package `--package` (a pkg:deb purl);
- the SBOM lists CPython `--python`.x (Syft's binary classifier: pkg:generic/python@3.12.15);
- the full Grype report (JSON) holds the vulnerability `--vulnerability` in `--package`.

The canary vulnerability is one Debian has not fixed (CVE-2026-85091 in zlib1g, today). When a
new base digest fixes it, this fails: pick another known vulnerability of a base package, or
check only the package. Standard library only; prints fixed texts.

    python3 -m scripts.check_scan_sees --sbom antifaz-image.cdx.json --grype grype-report.json \
        --package zlib1g --vulnerability CVE-2026-85091 --python 3.12
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _components(sbom: dict[str, Any]) -> list[dict[str, Any]]:
    components = sbom.get("components", [])
    return [c for c in components if isinstance(c, dict)] if isinstance(components, list) else []


def has_deb_package(sbom: dict[str, Any], package: str) -> bool:
    return any(
        c.get("name") == package and str(c.get("purl", "")).startswith(f"pkg:deb/debian/{package}@")
        for c in _components(sbom)
    )


def has_cpython(sbom: dict[str, Any], minor: str) -> bool:
    return any(
        c.get("name") == "python"
        and str(c.get("purl", "")).startswith(f"pkg:generic/python@{minor}.")
        and str(c.get("version", "")).startswith(f"{minor}.")
        for c in _components(sbom)
    )


def _field(match: object, name: str, key: str) -> object:
    """match[name][key], or None if any level is not a JSON object (a malformed report)."""
    inner = match.get(name) if isinstance(match, dict) else None
    return inner.get(key) if isinstance(inner, dict) else None


def has_vulnerability(report: dict[str, Any], vulnerability: str, package: str) -> bool:
    matches = report.get("matches", [])
    if not isinstance(matches, list):
        return False
    return any(
        _field(m, "vulnerability", "id") == vulnerability
        and _field(m, "artifact", "name") == package
        for m in matches
    )


def check(
    sbom: dict[str, Any], report: dict[str, Any], *, package: str, vulnerability: str, python: str
) -> list[str]:
    """The problems found (empty when the scanners see the three canaries)."""
    problems = []
    if not has_deb_package(sbom, package):
        problems.append(f"the SBOM does not list the Debian package {package}")
    if not has_cpython(sbom, python):
        problems.append(f"the SBOM does not list CPython {python}.x")
    if not has_vulnerability(report, vulnerability, package):
        problems.append(f"the Grype report does not hold {vulnerability} in {package}")
    return problems


def _load(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--grype", type=Path, required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--vulnerability", required=True)
    parser.add_argument("--python", required=True)
    args = parser.parse_args(argv)
    sbom, report = _load(args.sbom), _load(args.grype)
    unreadable = [p.name for p, d in ((args.sbom, sbom), (args.grype, report)) if d is None]
    if unreadable:
        print(f"check_scan_sees: cannot read {', '.join(unreadable)} as a JSON object")
        return 1
    assert sbom is not None and report is not None
    problems = check(
        sbom, report, package=args.package, vulnerability=args.vulnerability, python=args.python
    )
    for problem in problems:
        print(f"check_scan_sees: {problem}")
    if problems:
        print("check_scan_sees: FAIL (are the dpkg records of /var/lib/dpkg/status.d gone?)")
        return 1
    print(
        f"check_scan_sees: the scanners see {args.package}, {args.vulnerability} "
        f"and CPython {args.python}.x"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
