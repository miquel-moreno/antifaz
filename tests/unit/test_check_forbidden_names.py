"""The forbidden-names pre-commit hook (scripts/check_forbidden_names.py).

Only fake names here ("foobarname", "quuxname"): the real list lives outside the repository.
"""

import struct
import zlib
from pathlib import Path

import pytest
from scripts import check_forbidden_names as hook

NAME = "foobarname"


@pytest.fixture
def names_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "private" / "names.txt"
    path.parent.mkdir()
    path.write_text(
        "# a comment\n\nFooBarName\nquuxname\nallow:SafeFooBarNameWord\n", encoding="utf-8"
    )
    monkeypatch.setenv(hook.ENV_VAR, str(path))
    return path


def run(capsys: pytest.CaptureFixture[str], *files: Path) -> tuple[int, str]:
    code = hook.main([str(f) for f in files])
    out = capsys.readouterr()
    return code, out.out + out.err


def png(*chunks: tuple[bytes, bytes]) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body)
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    body = b"".join(chunk(k, b) for k, b in [(b"IHDR", ihdr), *chunks, (b"IEND", b"")])
    return b"\x89PNG\r\n\x1a\n" + body


def jpeg(*segments: tuple[int, bytes], pixels: bytes = b"") -> bytes:
    out = b"\xff\xd8"
    for marker, body in segments:
        out += bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body
    sos = b"\x01\x01\x00\x00\x3f\x00"
    return out + b"\xff\xda" + struct.pack(">H", len(sos) + 2) + sos + pixels + b"\xff\xd9"


def webp(*chunks: tuple[bytes, bytes]) -> bytes:
    body = b"WEBP" + b"".join(
        k + struct.pack("<I", len(b)) + b + (b"\0" if len(b) & 1 else b"") for k, b in chunks
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def gif(comment: bytes = b"", pixels: bytes = b"\x01\x00") -> bytes:
    head = b"GIF89a" + struct.pack("<HH", 1, 1) + b"\x80\x00\x00" + b"\x00" * 6  # 2-colour table
    ext = b"\x21\xfe" + bytes([len(comment)]) + comment + b"\x00" if comment else b""
    image = b"\x2c" + struct.pack("<HHHH", 0, 0, 1, 1) + b"\x00" + b"\x02"
    image += bytes([len(pixels)]) + pixels + b"\x00"
    return head + ext + image + b"\x3b"


def test_text_hit_reports_file_and_line_but_never_the_name(
    names_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "notes.md"
    f.write_text("first line\nsecond with FOOBARNAME inside\nthird\n", encoding="utf-8")
    code, out = run(capsys, f)
    assert code == 1
    assert f"forbidden name in {f.as_posix()}:2" in out
    assert NAME not in out.lower()


def test_clean_files_pass(
    names_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "clean.py"
    f.write_text("print('hello')\n", encoding="utf-8")
    assert run(capsys, f) == (0, "")


def test_allowlisted_words_are_skipped_but_other_matches_on_the_line_still_count(
    names_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ok = tmp_path / "ok.py"
    ok.write_text("from x import safefoobarnameword\n", encoding="utf-8")
    assert run(capsys, ok)[0] == 0
    bad = tmp_path / "bad.py"
    bad.write_text("SafeFooBarNameWord and foobarname\n", encoding="utf-8")
    assert run(capsys, bad)[0] == 1


def test_latin1_text_is_read_too(
    names_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "old.txt"
    f.write_bytes("caf\xe9 quuxname\n".encode("latin-1"))
    assert run(capsys, f) == (1, f"forbidden name in {f.as_posix()}:1\n")


def test_a_name_in_the_path_is_reported_masked(
    names_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "about-quuxname.txt"
    f.write_text("nothing\n", encoding="utf-8")
    code, out = run(capsys, f)
    assert code == 1
    assert "forbidden name in the path of" in out
    assert "about-***.txt" in out
    assert "quuxname" not in out


def test_other_binary_files_are_skipped(
    names_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "font.woff2"
    f.write_bytes(b"wOF2\0\0\0" + NAME.encode())
    assert run(capsys, f)[0] == 0


@pytest.mark.parametrize(
    "chunk",
    [
        (b"tEXt", b"Author\0" + NAME.encode()),
        (b"zTXt", b"Comment\0\0" + zlib.compress(NAME.encode())),
        (b"iTXt", b"XML:com.adobe.xmp\0\0\0en\0\0<x>" + NAME.encode() + b"</x>"),
        (b"iTXt", b"Title\0\1\0es\0\0" + zlib.compress(NAME.encode())),
        (b"eXIf", b"MM\0*\0\0\0\x08" + NAME.encode()),
        (b"iCCP", NAME.encode() + b"\0\0" + zlib.compress(b"profile")),
    ],
)
def test_png_metadata_is_scanned(
    names_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    chunk: tuple[bytes, bytes],
) -> None:
    f = tmp_path / "shot.png"
    f.write_bytes(png(chunk, (b"IDAT", zlib.compress(b"\0\0"))))
    assert run(capsys, f) == (1, f"forbidden name in {f.as_posix()} (in metadata)\n")


def test_png_pixels_are_never_scanned(
    names_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "shot.png"
    f.write_bytes(png((b"IDAT", NAME.encode()), (b"tEXt", b"Software\0Chromium")))
    assert run(capsys, f)[0] == 0


@pytest.mark.parametrize(
    "segment",
    [
        (0xE1, b"Exif\0\0MM\0*" + NAME.encode()),
        (0xE1, b"http://ns.adobe.com/xap/1.0/\0<x>" + NAME.encode() + b"</x>"),
        (0xE1, b"Exif\0\0" + NAME.encode("utf-16-le")),  # Windows XP tags are UTF-16
        (0xED, b"Photoshop 3.0\0" + NAME.encode()),
        (0xFE, NAME.upper().encode()),
    ],
)
def test_jpeg_metadata_is_scanned(
    names_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    segment: tuple[int, bytes],
) -> None:
    f = tmp_path / "shot.jpg"
    f.write_bytes(jpeg((0xE0, b"JFIF\0\1\1\0\0\1\0\1\0\0"), segment))
    assert run(capsys, f)[0] == 1


def test_jpeg_pixels_are_never_scanned(
    names_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "shot.jpeg"
    f.write_bytes(jpeg((0xE0, b"JFIF\0\1\1\0\0\1\0\1\0\0"), pixels=NAME.encode()))
    assert run(capsys, f)[0] == 0


def test_webp_metadata_but_not_pixels(
    names_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clean = tmp_path / "a.webp"
    clean.write_bytes(webp((b"VP8 ", NAME.encode())))
    assert run(capsys, clean)[0] == 0
    for kind in (b"EXIF", b"XMP "):
        bad = tmp_path / "b.webp"
        bad.write_bytes(webp((b"VP8 ", b"\0" * 10), (kind, NAME.encode())))
        assert run(capsys, bad)[0] == 1


def test_gif_comment_but_not_pixels(
    names_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clean = tmp_path / "a.gif"
    clean.write_bytes(gif(pixels=NAME.encode()))
    assert run(capsys, clean)[0] == 0
    bad = tmp_path / "b.gif"
    bad.write_bytes(gif(comment=NAME.encode()))
    assert run(capsys, bad)[0] == 1


def test_svg_is_text(names_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "logo.svg"
    f.write_text(f'<svg xmlns="http://www.w3.org/2000/svg">\n<title>{NAME}</title>\n</svg>\n')
    assert run(capsys, f) == (1, f"forbidden name in {f.as_posix()}:2\n")


def no_list(kind: str, tmp_path: Path) -> Path:
    """A list path that gives no names: missing, an empty file or a file with only comments."""
    path = tmp_path / "names.txt"
    if kind == "empty":
        path.write_bytes(b"")
    elif kind == "comments":
        path.write_text("# only comments\nallow:word\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("kind", ["missing", "empty", "comments"])
@pytest.mark.parametrize("ci", [None, "false", "1"])
def test_without_a_list_it_fails_locally_and_says_where_to_put_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    ci: str | None,
) -> None:
    if ci is None:
        monkeypatch.delenv("CI", raising=False)
    else:
        monkeypatch.setenv("CI", ci)  # only "true" counts as CI
    monkeypatch.setenv(hook.ENV_VAR, str(no_list(kind, tmp_path)))
    f = tmp_path / "x.txt"
    f.write_text(NAME, encoding="utf-8")
    code = hook.main([str(f)])
    err = capsys.readouterr().err
    assert code == 1
    assert err.startswith("error: no forbidden-names list at")
    assert str(hook.DEFAULT_LIST) in err  # the default path
    assert hook.ENV_VAR in err  # and the variable that overrides it


@pytest.mark.parametrize("kind", ["missing", "empty", "comments"])
@pytest.mark.parametrize("ci", ["true", "TRUE", " True "])
def test_without_a_list_it_only_warns_in_ci(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    ci: str,
) -> None:
    monkeypatch.setenv("CI", ci)
    monkeypatch.setenv(hook.ENV_VAR, str(no_list(kind, tmp_path)))
    f = tmp_path / "x.txt"
    f.write_text(NAME, encoding="utf-8")
    code = hook.main([str(f)])
    out = capsys.readouterr()
    assert code == 0
    assert out.err.startswith("warning: no forbidden-names list at")
    assert out.out == ""


def test_the_default_list_is_outside_the_repository() -> None:
    assert hook.ROOT not in hook.DEFAULT_LIST.parents
    assert hook.DEFAULT_LIST.parent.name == "_privado"


def test_truncated_images_do_not_crash(
    names_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, data in [
        ("a.png", png((b"zTXt", b"k\0\0not-zlib"))[:40]),
        ("b.jpg", b"\xff\xd8\xff\xff\xff\xd0\xff"),
        ("c.gif", b"GIF89a"),
        ("d.gif", gif()[:-1] + b"\x99"),
    ]:
        f = tmp_path / name
        f.write_bytes(data)
        assert run(capsys, f)[0] == 0
