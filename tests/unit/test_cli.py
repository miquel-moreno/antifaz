"""Command line: `antifaz scan` and `antifaz mask` never print values or the table."""

import io
from pathlib import Path

import pytest

from antifaz.cli import main

DNI = "12345678Z"  # synthetic
TEXT = f"Mi DNI es {DNI} y mi correo ana@example.com\n"


def write(tmp_path: Path, content: bytes) -> Path:
    path = tmp_path / "input.txt"
    path.write_bytes(content)
    return path


def test_scan_prints_type_and_positions_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["scan", str(write(tmp_path, TEXT.encode()))]) == 0
    out = capsys.readouterr().out
    assert out.splitlines() == ["ES_DNI 10 19", "EMAIL 32 47"]
    assert DNI not in out


def test_mask_prints_masked_text_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["mask", str(write(tmp_path, TEXT.encode()))]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Mi DNI es [[ES_DNI_1]] y mi correo [[EMAIL_1]]\n"
    assert DNI not in captured.out + captured.err


def test_dash_reads_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stdin = io.TextIOWrapper(io.BytesIO(TEXT.encode()), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", stdin)
    assert main(["mask", "-"]) == 0
    assert "[[ES_DNI_1]]" in capsys.readouterr().out


def test_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", str(tmp_path / "nope.txt")]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "nope" not in captured.err
    assert captured.err.strip()


def test_invalid_utf8_does_not_echo_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, b"\xff" + DNI.encode())
    assert main(["mask", str(path)]) == 2
    captured = capsys.readouterr()
    assert DNI not in captured.out + captured.err
    assert captured.out == ""


def test_detector_failure_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(text: str) -> list[object]:
        raise RuntimeError(text)

    monkeypatch.setattr("antifaz.cli.scan", broken)
    assert main(["scan", str(write(tmp_path, TEXT.encode()))]) == 2
    assert DNI not in capsys.readouterr().err


def test_no_command_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as info:
        main([])
    assert info.value.code == 2


def test_stray_arguments_are_not_echoed(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["scan", "a.txt", DNI])
    assert info.value.code == 2
    captured = capsys.readouterr()
    assert DNI not in captured.out + captured.err
    assert captured.err.strip()


def test_output_is_utf8_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = io.BytesIO()
    stdout = io.TextIOWrapper(buffer, encoding="cp1252")
    monkeypatch.setattr("sys.stdout", stdout)
    path = write(tmp_path, "¿Ñandú? 中 DNI 12345678Z\n".encode())
    assert main(["mask", str(path)]) == 0
    stdout.flush()
    assert buffer.getvalue().decode("utf-8") == "¿Ñandú? 中 DNI [[ES_DNI_1]]\n"
