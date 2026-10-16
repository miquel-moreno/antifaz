"""Fail the CI if the scanners go blind on the image (issue 54, ADR-0019).

On the distroless image the Debian packages are visible only through their records in
/var/lib/dpkg/status.d, and CPython only through its binary. If a record went missing (or a
library were copied without one), Syft would list fewer packages, Grype would report fewer
vulnerabilities, and the gate would pass for the wrong reason.

Miquel's rule: the STRUCTURE blocks, the known CVE only warns.

Blocking (exit 1, a GitHub `::error::` line for each problem):
- the Grype report (JSON) sees the distro `--distro` `--distro-version`.x (debian 13);
- Grype matched at least one Debian package (`artifact.type == "deb"`);
- the SBOM (Syft, CycloneDX JSON) lists every `--deb` package as a pkg:deb/debian purl
  (libc6, libssl3t64, zlib1g);
- the SBOM lists CPython `--python`.x (Syft's binary classifier: pkg:generic/python@3.12.15).

Warning only (exit 0, a GitHub `::warning::` line): the report no longer holds
`--warn-vulnerability` in `--warn-package` (CVE-2026-85091 in zlib1g today). Debian may have
fixed or reclassified it; then pick another canary.

Standard library only; prints fixed texts.

    python3 -m scripts.check_scan_sees --sbom antifaz-image.cdx.json --grype grype-report.json \
        --distro debian --distro-version 13 --deb libc6 --deb libssl3t64 --deb zlib1g \
        --python 3.12 --warn-vulnerability CVE-2026-85091 --warn-package zlib1g
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def _get(data: object, *keys: str) -> object:
    """data[k1][k2]..., or None as soon as a level is not a JSON object (malformed input)."""
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data


def _list(data: object, key: str) -> list[object]:
    value = _get(data, key)
    return value if isinstance(value, list) else []


def has_deb_package(sbom: dict[str, Any], package: str) -> bool:
    return any(
        _get(c, "name") == package
        and str(_get(c, "purl") or "").startswith(f"pkg:deb/debian/{package}@")
        for c in _list(sbom, "components")
    )


def has_cpython(sbom: dict[str, Any], minor: str) -> bool:
    return any(
        _get(c, "name") == "python"
        and str(_get(c, "purl") or "").startswith(f"pkg:generic/python@{minor}.")
        and str(_get(c, "version") or "").startswith(f"{minor}.")
        for c in _list(sbom, "components")
    )


def check_sbom(sbom: dict[str, Any], *, deb_packages: Sequence[str], python: str) -> list[str]:
    """Structural problems of the SBOM (blocking)."""
    problems = [
        f"the SBOM does not list the Debian package {package}"
        for package in deb_packages
        if not has_deb_package(sbom, package)
    ]
    if not has_cpython(sbom, python):
        problems.append(f"the SBOM does not list CPython {python}.x")
    return problems


def check_report(report: dict[str, Any], *, distro: str, distro_version: str) -> list[str]:
    """Structural problems of the Grype report (blocking)."""
    problems = []
    name, version = _get(report, "distro", "name"), _get(report, "distro", "version")
    if name != distro or not (
        str(version) == distro_version or str(version).startswith(f"{distro_version}.")
    ):
        shown = [value if isinstance(value, str) else "?" for value in (name, version)]
        seen = " ".join(shown)
        problems.append(f"Grype does not see Debian {distro_version} (it sees {seen})")
    if not any(_get(m, "artifact", "type") == "deb" for m in _list(report, "matches")):
        problems.append("Grype matched no Debian (deb) package")
    return problems


def canary_warnings(report: dict[str, Any], *, vulnerability: str, package: str) -> list[str]:
    """A warning when the known vulnerability is no longer reported (never blocking)."""
    seen = any(
        _get(m, "vulnerability", "id") == vulnerability and _get(m, "artifact", "name") == package
        for m in _list(report, "matches")
    )
    if seen:
        return []
    return [
        f"the Grype report no longer holds {vulnerability} in {package}: Debian may have fixed "
        "or reclassified it (check the full report and pick another canary)"
    ]


def check(
    sbom: dict[str, Any],
    report: dict[str, Any],
    *,
    distro: str,
    distro_version: str,
    deb_packages: Sequence[str],
    python: str,
    vulnerability: str,
    package: str,
) -> tuple[list[str], list[str]]:
    """(blocking problems, warnings)."""
    problems = check_sbom(sbom, deb_packages=deb_packages, python=python)
    problems += check_report(report, distro=distro, distro_version=distro_version)
    return problems, canary_warnings(report, vulnerability=vulnerability, package=package)


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
    parser.add_argument("--distro", required=True)
    parser.add_argument("--distro-version", required=True)
    parser.add_argument("--deb", action="append", required=True, dest="debs")
    parser.add_argument("--python", required=True)
    parser.add_argument("--warn-vulnerability", required=True)
    parser.add_argument("--warn-package", required=True)
    args = parser.parse_args(argv)
    sbom, report = _load(args.sbom), _load(args.grype)
    unreadable = [p.name for p, d in ((args.sbom, sbom), (args.grype, report)) if d is None]
    if unreadable:
        print(f"::error::check_scan_sees: cannot read {', '.join(unreadable)} as a JSON object")
        return 1
    assert sbom is not None and report is not None
    problems, warnings = check(
        sbom,
        report,
        distro=args.distro,
        distro_version=args.distro_version,
        deb_packages=args.debs,
        python=args.python,
        vulnerability=args.warn_vulnerability,
        package=args.warn_package,
    )
    for warning in warnings:
        print(f"::warning::check_scan_sees: {warning}")
    for problem in problems:
        print(f"::error::check_scan_sees: {problem}")
    if problems:
        print("check_scan_sees: FAIL (are the dpkg records of /var/lib/dpkg/status.d gone?)")
        return 1
    print(
        f"check_scan_sees: the scanners see {args.distro} {args.distro_version}, deb matches, "
        f"{', '.join(args.debs)} and CPython {args.python}.x"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
