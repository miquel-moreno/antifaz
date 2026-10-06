"""The docker-compose.yml attached to each GitHub release, with the image pinned by digest.

Issue 42, ADR-0017. release.yml runs it after pushing the image:

    python3 -m scripts.release_compose --version 0.2.0 --digest sha256:<64 hex> --output FILE

It copies the repository's docker-compose.yml and changes only the image line, from
`ghcr.io/miquel-moreno/antifaz:${ANTIFAZ_VERSION:-X.Y.Z}` to
`ghcr.io/miquel-moreno/antifaz:X.Y.Z@sha256:...`: whoever downloads it runs exactly the image
that was built, tested and attested, even if the tag were moved later. Standard library only
(the release job installs nothing). Any surprise is an error, never a guess.
"""

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IMAGE = "ghcr.io/miquel-moreno/antifaz"
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_IMAGE_LINE = re.compile(
    r"^(?P<indent>[ ]+)image: ghcr\.io/miquel-moreno/antifaz:\$\{ANTIFAZ_VERSION:-"
    r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+)\}$",
    re.MULTILINE,
)


def pinned(compose: str, version: str, digest: str) -> str:
    """`compose` with its one image line pinned to `version@digest`. Raises ValueError."""
    if not _VERSION.fullmatch(version):
        raise ValueError("the version must be X.Y.Z")
    if not _DIGEST.fullmatch(digest):
        raise ValueError("the digest must be sha256: and 64 lowercase hex characters")
    lines = list(_IMAGE_LINE.finditer(compose))
    if len(lines) != 1:
        raise ValueError(f"expected one image line in docker-compose.yml, found {len(lines)}")
    (line,) = lines
    if line["version"] != version:
        raise ValueError("docker-compose.yml names another version than the release")
    replacement = f"{line['indent']}image: {IMAGE}:{version}@{digest}"
    header = (
        f"# Antifaz {version}, attached to the GitHub release v{version}. The image is pinned by\n"
        "# digest: it is exactly the one built and attested by release.yml.\n"
        "# Check it: gh attestation verify oci://"
        f"{IMAGE}:{version} --repo miquel-moreno/antifaz\n"
        "#\n"
    )
    return header + compose[: line.start()] + replacement + compose[line.end() :]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--version", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--compose", type=Path, default=REPO / "docker-compose.yml")
    args = parser.parse_args(argv)
    try:
        text = pinned(args.compose.read_text(encoding="utf-8"), args.version, args.digest)
    except ValueError as error:
        print(f"release_compose: {error}", file=sys.stderr)
        return 1
    args.output.write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
