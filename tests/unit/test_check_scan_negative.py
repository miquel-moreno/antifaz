"""scripts/check_scan_negative.py: the local proof that check_scan_sees catches a blind scan.

The script (run by hand, it needs Docker and the Syft image) builds a copy of the image with the
same files but without /var/lib/dpkg, runs Syft on both and expects the structural SBOM check
to pass on the real image and fail on the copy. These tests cover its pure parts.
"""

from pathlib import Path

import yaml
from scripts.check_scan_negative import SYFT_IMAGE, nodpkg_dockerfile, verdict

REPO = Path(__file__).resolve().parents[2]


def test_the_copy_keeps_the_files_but_not_the_dpkg_records() -> None:
    dockerfile = nodpkg_dockerfile("antifaz:local")
    lines = [line.strip() for line in dockerfile.splitlines() if line.strip()]
    assert lines[0] == "FROM antifaz:local AS src"
    assert "FROM scratch" in lines
    copies = [line for line in lines if line.startswith("COPY")]
    assert copies == [
        "COPY --from=src /usr /usr",
        "COPY --from=src /etc /etc",
        "COPY --from=src /app /app",
    ]
    assert "/var" not in dockerfile  # /var/lib/dpkg/status.d stays behind
    assert "RUN" not in dockerfile


def test_syft_is_pinned_to_the_ci_version() -> None:
    ci = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    (sbom,) = [
        s for s in ci["jobs"]["image"]["steps"] if "anchore/sbom-action@" in str(s.get("uses"))
    ]
    assert SYFT_IMAGE == f"anchore/syft:{sbom['with']['syft-version']}" == "anchore/syft:v1.51.1"


def test_the_verdict_needs_a_pass_on_the_image_and_a_failure_on_the_copy() -> None:
    assert verdict([], ["the SBOM does not list the Debian package libc6"]) is True
    assert verdict([], []) is False  # the check missed the blind scan
    assert verdict(["x"], ["y"]) is False  # the real image fails: something else is wrong
