"""The CI helper scripts: license rule, privacy-coverage rule and the design-check server."""

import http.client
import json
import subprocess
from collections.abc import Iterator
from importlib import metadata
from pathlib import Path

import pytest
from scripts import check_licenses
from scripts.check_coverage import check, main, run
from scripts.check_licenses import Verdict, classify, documented_packages


@pytest.mark.parametrize(
    ("texts", "verdict"),
    [
        (["MIT"], Verdict.OK),
        (["Apache-2.0"], Verdict.OK),
        (["", "License :: OSI Approved :: BSD License"], Verdict.OK),
        (["PSF-2.0"], Verdict.OK),
        (["LGPL-2.1-or-later"], Verdict.WEAK_COPYLEFT),
        (["MPL-2.0"], Verdict.WEAK_COPYLEFT),
        (
            ["", "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)"],
            Verdict.WEAK_COPYLEFT,
        ),
        (["GPL-3.0-only"], Verdict.FORBIDDEN),
        (["AGPL-3.0-or-later"], Verdict.FORBIDDEN),
        (
            ["", "License :: OSI Approved :: GNU General Public License v3 (GPLv3)"],
            Verdict.FORBIDDEN,
        ),
        (["CC-BY-NC-ND-4.0"], Verdict.FORBIDDEN),
        (["SSPL-1.0"], Verdict.FORBIDDEN),
        (["MIT OR GPL-2.0"], Verdict.FORBIDDEN),  # any strong copyleft option is flagged
        ([""], Verdict.UNKNOWN),
        (["UNKNOWN"], Verdict.UNKNOWN),
        (["Copyright (c) someone"], Verdict.UNKNOWN),
    ],
)
def test_license_classification(texts: list[str], verdict: Verdict) -> None:
    assert classify(texts) is verdict


def coverage_report(files: dict[str, tuple[int, int]]) -> dict[str, object]:
    return {
        "files": {
            path: {"summary": {"covered_lines": covered, "num_statements": statements}}
            for path, (covered, statements) in files.items()
        }
    }


def test_privacy_pieces_need_ninety_percent() -> None:
    report = coverage_report(
        {
            "src/antifaz/detect/validators/dni.py": (19, 20),  # 95 % -> ok
            "src\\antifaz\\mask\\core.py": (8, 10),  # 80 % -> too low (Windows path)
            "src/antifaz/cli/__init__.py": (1, 10),  # not a privacy piece
        }
    )

    assert check(report) == ["mask: 80.0 % < 90 %"]


def test_the_virtual_keys_are_a_privacy_piece() -> None:
    report = coverage_report({"src/antifaz/keys/store.py": (85, 100)})

    assert check(report) == ["keys: 85.0 % < 90 %"]


def test_the_database_code_is_a_privacy_piece() -> None:
    report = coverage_report({"src/antifaz/db/connect.py": (85, 100)})

    assert check(report) == ["db: 85.0 % < 90 %"]


def test_a_piece_can_be_left_out_when_its_tests_did_not_run(tmp_path: Path) -> None:
    """`make test` without PostgreSQL: the db piece is skipped (CI never passes --without)."""
    path = tmp_path / "coverage.json"
    report = coverage_report({"src/antifaz/db/connect.py": (10, 100)})
    path.write_text(json.dumps(report), encoding="utf-8")

    assert check(report, without=frozenset({"db"})) == []
    assert run(["--without", "db"], path) == 0
    assert run([], path) == 1


@pytest.mark.parametrize("argv", [["--without", "nothing"], ["--without"], ["--other"]])
def test_an_unknown_piece_or_option_is_refused(tmp_path: Path, argv: list[str]) -> None:
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(coverage_report({})), encoding="utf-8")

    with pytest.raises(SystemExit) as stop:
        run(argv, path)
    assert stop.value.code == 2


def test_paths_outside_the_package_do_not_count() -> None:
    assert check(coverage_report({"detect/other_project.py": (0, 10)})) == []


def test_a_missing_coverage_file_is_a_clear_failure(tmp_path: Path) -> None:
    assert main(tmp_path / "coverage.json") == 1


def test_pieces_without_code_are_skipped() -> None:
    assert check(coverage_report({"src/antifaz/guard/__init__.py": (0, 0)})) == []


def test_only_table_rows_count_as_documented() -> None:
    doc = (
        "certifi is mentioned here in passing.\n\n"
        "| Paquete | Licencia |\n|---|---|\n| python-stdnum | LGPL |\n"
    )

    assert documented_packages(doc) == {"python-stdnum"}


def test_validators_need_ninety_five_percent() -> None:
    report = coverage_report({"src/antifaz/detect/validators/dni.py": (93, 100)})

    assert check(report) == ["detect/validators: 93.0 % < 95 %"]


def test_license_check_reads_the_optional_extras_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `ner` extra ships with Antifaz too: its locked packages are checked like the rest."""
    seen: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="fastapi==1.0\ntorch==2.0 ; x\n")

    monkeypatch.setattr(check_licenses.subprocess, "run", fake_run)

    assert check_licenses.runtime_dependencies() == [("fastapi", False), ("torch", True)]
    assert "--all-extras" in seen[0]


def test_a_package_of_an_extra_that_is_not_installed_uses_its_recorded_license(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CI never installs torch: the licenses of the extra, read by hand from their metadata,
    stand in for the missing metadata."""

    def missing(name: str) -> object:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(check_licenses.metadata, "metadata", missing)

    assert check_licenses.license_texts("torch") == [check_licenses.RECORDED_EXTRA["torch"]]
    with pytest.raises(metadata.PackageNotFoundError):
        check_licenses.license_texts("not-a-recorded-package")


def test_every_recorded_license_is_allowed_and_weak_copyleft_is_documented() -> None:
    documented = documented_packages(check_licenses.LICENSES_DOC.read_text(encoding="utf-8"))
    for name, text in check_licenses.RECORDED_EXTRA.items():
        verdict = classify([text])
        assert verdict in (Verdict.OK, Verdict.WEAK_COPYLEFT), name
        if verdict is Verdict.WEAK_COPYLEFT:
            assert name in documented, name


def test_every_package_only_the_extras_bring_has_a_recorded_license() -> None:
    """Otherwise CI (without the extra) could not check it and the license job would fail."""
    with_extras = {name for name, _ in check_licenses.runtime_dependencies()}
    without = {name for name, _ in check_licenses.runtime_dependencies(extras=False)}

    assert with_extras - without <= set(check_licenses.RECORDED_EXTRA)


def test_the_extra_record_holds_only_packages_that_only_the_extras_bring() -> None:
    """A package that also ships without the extras is checked from its installed metadata:
    a stale record would hide what its metadata says now."""
    with_extras = {name for name, _ in check_licenses.runtime_dependencies()}
    without = {name for name, _ in check_licenses.runtime_dependencies(extras=False)}

    assert set(check_licenses.RECORDED_EXTRA) <= with_extras - without


def test_jinja2_ships_without_any_extra() -> None:
    """The panel's templates (ADR-0018): Jinja2 and MarkupSafe are direct runtime packages."""
    without = {name for name, _ in check_licenses.runtime_dependencies(extras=False)}

    assert {"jinja2", "markupsafe"} <= without
    assert classify(check_licenses.license_texts("jinja2")) is Verdict.OK
    assert classify(check_licenses.license_texts("markupsafe")) is Verdict.OK


def test_recorded_licenses_match_the_installed_metadata() -> None:
    """Where the extra is installed, what was recorded by hand must say the same."""
    checked = 0
    for name, text in check_licenses.RECORDED_EXTRA.items():
        try:
            meta = metadata.metadata(name)
        except metadata.PackageNotFoundError:
            continue
        checked += 1
        assert classify(check_licenses.metadata_texts(meta)) is classify([text]), name
    if not checked:
        pytest.skip("the ner extra is not installed")


# --- the `bench` dependency group (Presidio baseline, issue 13): never shipped ------------


def test_the_shipped_dependencies_never_bring_presidio_or_spacy() -> None:
    """`uv export --no-dev` (what ships, every extra included) lists none of the bench group."""
    shipped = {name for name, _ in check_licenses.runtime_dependencies()}
    assert not shipped & {"presidio-analyzer", "spacy", "xx-ent-wiki-sm", "thinc"}


def test_the_bench_group_is_read_from_uv_lock() -> None:
    bench = {name for name, _ in check_licenses.bench_dependencies()}
    assert {"presidio-analyzer", "spacy", "xx-ent-wiki-sm"} <= bench


def test_every_package_only_the_bench_group_brings_has_a_recorded_license() -> None:
    """CI never installs the bench group: its licenses, read by hand, stand in for metadata."""
    shipped = {name for name, _ in check_licenses.runtime_dependencies()}
    bench = {name for name, _ in check_licenses.bench_dependencies()}
    assert bench - shipped <= set(check_licenses.RECORDED_BENCH)


def test_every_recorded_bench_license_is_allowed() -> None:
    documented = documented_packages(check_licenses.LICENSES_DOC.read_text(encoding="utf-8"))
    for name, text in check_licenses.RECORDED_BENCH.items():
        verdict = classify([text])
        assert verdict in (Verdict.OK, Verdict.WEAK_COPYLEFT), name
        assert name in documented, name  # every bench package is listed in docs/licencias.md


def test_recorded_bench_licenses_match_the_installed_metadata() -> None:
    checked = 0
    for name, text in check_licenses.RECORDED_BENCH.items():
        try:
            meta = metadata.metadata(name)
        except metadata.PackageNotFoundError:
            continue
        checked += 1
        assert classify(check_licenses.metadata_texts(meta)) is classify([text]), name
    if not checked:
        pytest.skip("the bench group is not installed")


def test_a_forbidden_license_in_the_bench_group_fails_the_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(check_licenses, "runtime_dependencies", lambda extras=True: [])
    monkeypatch.setattr(check_licenses, "bench_dependencies", lambda: [("es-core-news-sm", False)])
    monkeypatch.setattr(check_licenses, "license_texts", lambda name: ["GPL-3.0-only"])

    assert check_licenses.main() == 1
    assert "es-core-news-sm" in capsys.readouterr().err


# --- The local server of make design-check (scripts/design_server.py) -------------------------

SENTINEL = b"sentinel-outside-the-allowed-folders"


@pytest.fixture
def design_server(tmp_path: Path) -> Iterator[tuple[str, int]]:
    from scripts.design_server import serve

    (tmp_path / "design").mkdir()
    (tmp_path / "design" / "ok.txt").write_bytes(b"inside")
    (tmp_path / "docs" / "images").mkdir(parents=True)
    (tmp_path / "outside-secret.txt").write_bytes(SENTINEL)
    (tmp_path / "designx").mkdir()
    (tmp_path / "designx" / "outside-secret.txt").write_bytes(SENTINEL)
    server, _ = serve(tmp_path, ("/design/", "/docs/images/"))
    try:
        yield "127.0.0.1", server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _raw(address: tuple[str, int], method: str, path: str) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection(*address, timeout=10)
    try:
        connection.putrequest(method, path, skip_accept_encoding=True)
        connection.endheaders()
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_the_design_server_serves_the_allowed_folders(design_server: tuple[str, int]) -> None:
    assert _raw(design_server, "GET", "/design/ok.txt") == (200, b"inside")


@pytest.mark.parametrize(
    "path",
    [
        "/design/../outside-secret.txt",
        "/design/%2e%2e/outside-secret.txt",
        "/design/%2E%2E%2Foutside-secret.txt",
        "/docs/images/..%2f..%2foutside-secret.txt",
        "/design/..%5coutside-secret.txt",
        "/design/..\\outside-secret.txt",
        "/design/./../designx/outside-secret.txt",
        "/designx/outside-secret.txt",
        "/outside-secret.txt",
        "//outside-secret.txt",
        "/design/%252e%252e/outside-secret.txt",
    ],
)
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_the_design_server_never_leaves_the_allowed_folders(
    design_server: tuple[str, int], path: str, method: str
) -> None:
    status, body = _raw(design_server, method, path)

    assert status in (403, 404), (method, path, status)
    assert SENTINEL not in body
