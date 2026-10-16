"""Local proof that scripts/check_scan_sees.py catches a blind scan (issue 54, ADR-0019).

Run by hand (needs Docker; not part of `make check` or `make e2e`, because it pulls the Syft
image, about 117 MB):

    uv run python -m scripts.check_scan_negative [IMAGE]        # default antifaz:local

It builds `antifaz-b2-nodpkg`, a copy of IMAGE with the same /usr, /etc and /app but without
/var/lib/dpkg (the dpkg records of distroless), runs Syft on both (no network, the version the CI
uses) and checks the structural SBOM rules of check_scan_sees on each: they must pass on IMAGE
and fail on the copy. The Grype half of the structure (Debian 13, deb matches) needs the Grype
vulnerability database and is left to the CI. The copy and the temporary files are removed at
the end; the Syft image stays (remove it with `docker rmi anchore/syft:v1.51.1`).
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts.check_scan_sees import check_sbom

SYFT_IMAGE = "anchore/syft:v1.51.1"  # the syft-version of the CI's sbom-action
COPY_TAG = "antifaz-b2-nodpkg"
DEBS = ("libc6", "libssl3t64", "zlib1g")


def nodpkg_dockerfile(base: str) -> str:
    """The same files as `base` without /var/lib/dpkg (never run; the copy is only scanned)."""
    return (
        f"FROM {base} AS src\n"
        "FROM scratch\n"
        "COPY --from=src /usr /usr\n"
        "COPY --from=src /etc /etc\n"
        "COPY --from=src /app /app\n"
    )


def verdict(image_problems: list[str], copy_problems: list[str]) -> bool:
    """True when the check passes on the real image and fails on the copy without records."""
    return not image_problems and bool(copy_problems)


def _docker(*args: str, stdin: str | None = None, timeout: float = 600) -> None:
    subprocess.run(  # noqa: S603 - fixed argument list, no shell
        ["docker", *args],  # noqa: S607 - docker from PATH
        input=stdin, text=True, check=True, timeout=timeout, capture_output=True,
    )  # fmt: skip


def _sbom(image: str, folder: Path, name: str) -> dict[str, object]:
    _docker("save", image, "-o", str(folder / f"{name}.tar"))
    _docker(
        "run", "--rm", "--network", "none", "-e", "SYFT_CHECK_FOR_APP_UPDATE=false",
        "-v", f"{folder}:/scan", SYFT_IMAGE, f"docker-archive:/scan/{name}.tar",
        "-o", f"cyclonedx-json=/scan/{name}.cdx.json", "-q",
    )  # fmt: skip
    data: dict[str, object] = json.loads((folder / f"{name}.cdx.json").read_text("utf-8"))
    return data


def main(argv: list[str] | None = None) -> int:
    image = (argv if argv is not None else sys.argv[1:] or ["antifaz:local"])[0]
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        try:
            _docker(
                "build", "--network", "none", "--tag", COPY_TAG, "-",
                stdin=nodpkg_dockerfile(image),
            )  # fmt: skip
            results = {}
            for name, tag in (("image", image), ("copy", COPY_TAG)):
                problems = check_sbom(_sbom(tag, folder, name), deb_packages=DEBS, python="3.12")
                results[name] = problems
                print(f"{tag}: {'PASS' if not problems else 'FAIL'}")
                for problem in problems:
                    print(f"  {problem}")
        finally:
            remove = ["docker", "rmi", "-f", COPY_TAG]
            subprocess.run(remove, capture_output=True, check=False)  # noqa: S603
    ok = verdict(results["image"], results["copy"])
    print(
        "check_scan_negative: OK, the structural check catches an image without dpkg records"
        if ok
        else "check_scan_negative: UNEXPECTED (see above)"
    )
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
