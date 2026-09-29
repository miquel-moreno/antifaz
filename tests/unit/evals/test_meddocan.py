"""MEDDOCAN loader: brat parsing, integrity check, test split and cached download.

Every fixture is invented here; nothing is copied from MEDDOCAN.
"""

import hashlib
import urllib.request
import zipfile
from pathlib import Path

import pytest
from evals.datasets import meddocan
from evals.datasets.meddocan import (
    MEDDOCAN_TO_ANTIFAZ,
    TEST_PREFIX,
    Document,
    download,
    load_test,
    parse_brat,
    verify,
)
from evals.metrics import Annotation

A = Annotation

# Invented clinical-style text with accents and Windows line endings: the "\r" characters
# count in the brat offsets, so a loader that turned "\r\n" into "\n" would shift them.
TEXT = (
    "Nombre: Íñigo Pérez Ñúñez.\r\n"
    "Domicilio: Calle Falsa, nº 12.\r\n"
    "Email: inigo.prueba@example.com\r\n"
)
ANN = (
    "T1\tNOMBRE_SUJETO_ASISTENCIA 8 25\tÍñigo Pérez Ñúñez\n"
    "T2\tCALLE 39 50;55 57\tCalle Falsa 12\n"
    "#1\tAnnotatorNotes T1\tnota inventada\n"
    "R1\tRelacion Arg1:T1 Arg2:T2\t\n"
    "T3\tCORREO_ELECTRONICO 67 91\tinigo.prueba@example.com\n"
)
EXPECTED = (
    A(8, 25, "NOMBRE_SUJETO_ASISTENCIA"),
    A(39, 50, "CALLE"),
    A(55, 57, "CALLE"),
    A(67, 91, "CORREO_ELECTRONICO"),
)

# The 29 entity types of the MEDDOCAN annotation guidelines.
MEDDOCAN_LABELS = [
    "NOMBRE_SUJETO_ASISTENCIA",
    "EDAD_SUJETO_ASISTENCIA",
    "SEXO_SUJETO_ASISTENCIA",
    "FAMILIARES_SUJETO_ASISTENCIA",
    "NOMBRE_PERSONAL_SANITARIO",
    "FECHAS",
    "PROFESION",
    "HOSPITAL",
    "CENTRO_SALUD",
    "INSTITUCION",
    "CALLE",
    "TERRITORIO",
    "PAIS",
    "NUMERO_TELEFONO",
    "NUMERO_FAX",
    "CORREO_ELECTRONICO",
    "ID_SUJETO_ASISTENCIA",
    "ID_CONTACTO_ASISTENCIAL",
    "ID_ASEGURAMIENTO",
    "ID_TITULACION_PERSONAL_SANITARIO",
    "ID_EMPLEO_PERSONA",
    "IDENTIF_VEHICULOS_NRSERIE_PRODUCTOS",
    "IDENTIF_DISPOSITIVOS_NRSERIE",
    "NUMERO_BENEF_PLAN_SALUD",
    "URL_WEB",
    "DIREC_PROT_INTERNET",
    "IDENTIF_BIOMETRICOS",
    "OTRO_NUMERO_IDENTIF",
    "OTROS_SUJETO_ASISTENCIA",
]


# --- parse_brat ------------------------------------------------------------------------


def test_parse_brat_reads_offsets_with_accents_and_crlf() -> None:
    assert parse_brat(TEXT, ANN) == EXPECTED


def test_parse_brat_splits_a_discontinuous_span_into_one_annotation_per_fragment() -> None:
    calle = [a for a in parse_brat(TEXT, ANN) if a.label == "CALLE"]
    assert calle == [A(39, 50, "CALLE"), A(55, 57, "CALLE")]


def test_parse_brat_ignores_notes_relations_and_blank_lines() -> None:
    ann = (
        "#1\tAnnotatorNotes T1\tnota\n"
        "\n"
        "R1\tRel Arg1:T1 Arg2:T3\t\n"
        "T3\tCORREO_ELECTRONICO 67 91\tinigo.prueba@example.com\n"
    )
    assert parse_brat(TEXT, ann) == (A(67, 91, "CORREO_ELECTRONICO"),)


def test_parse_brat_of_an_empty_ann_gives_no_annotations() -> None:
    assert parse_brat(TEXT, "") == ()


def test_parse_brat_accepts_crlf_line_endings_in_the_ann_file() -> None:
    assert parse_brat(TEXT, ANN.replace("\n", "\r\n")) == EXPECTED


def test_misaligned_offset_raises_without_revealing_the_value() -> None:
    ann = "T1\tCORREO_ELECTRONICO 68 92\tinigo.prueba@example.com\n"
    with pytest.raises(ValueError) as excinfo:
        parse_brat(TEXT, ann)
    message = str(excinfo.value)
    assert "inigo.prueba@example.com" not in message
    assert "nigo.prueba" not in message


def test_misaligned_discontinuous_span_raises_without_revealing_the_value() -> None:
    ann = "T2\tCALLE 39 50;54 57\tCalle Falsa 12\n"
    with pytest.raises(ValueError) as excinfo:
        parse_brat(TEXT, ann)
    assert "Calle Falsa" not in str(excinfo.value)


def test_offsets_counted_without_the_carriage_returns_are_rejected() -> None:
    # Same email, but offsets as if "\r\n" had been turned into "\n" (two lines earlier).
    ann = "T3\tCORREO_ELECTRONICO 65 89\tinigo.prueba@example.com\n"
    with pytest.raises(ValueError):
        parse_brat(TEXT, ann)


# --- verify ----------------------------------------------------------------------------


@pytest.fixture
def blob(tmp_path: Path) -> tuple[Path, str, int]:
    data = b"contenido inventado para comprobar la integridad\n"
    path = tmp_path / "blob.zip"
    path.write_bytes(data)
    return path, hashlib.md5(data, usedforsecurity=False).hexdigest(), len(data)


def test_verify_accepts_the_right_size_and_md5(blob: tuple[Path, str, int]) -> None:
    path, md5, size = blob
    verify(path, md5=md5, size=size)


def test_verify_rejects_a_wrong_size(blob: tuple[Path, str, int]) -> None:
    path, md5, size = blob
    with pytest.raises(ValueError):
        verify(path, md5=md5, size=size + 1)


def test_verify_rejects_a_wrong_md5(blob: tuple[Path, str, int]) -> None:
    path, _md5, size = blob
    with pytest.raises(ValueError):
        verify(path, md5="0" * 32, size=size)


def test_verify_with_the_defaults_rejects_a_file_that_is_not_meddocan(
    blob: tuple[Path, str, int],
) -> None:
    path, _, _ = blob
    with pytest.raises(ValueError):
        verify(path)


# --- load_test -------------------------------------------------------------------------

DOC_A_TEXT = "Paciente: Ana Inventada.\nTel: 600000000\n"
DOC_A_ANN = (
    "T1\tNOMBRE_SUJETO_ASISTENCIA 10 23\tAna Inventada\nT2\tNUMERO_TELEFONO 30 39\t600000000\n"
)
DOC_B_TEXT = "Linea uno.\r\nCorreo: b@example.com\r\n"
DOC_B_ANN = "T1\tCORREO_ELECTRONICO 20 33\tb@example.com\n"


def _build_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        # Written out of order on purpose: the loader must sort by name.
        zf.writestr(f"{TEST_PREFIX}doc-b.txt", DOC_B_TEXT.encode("utf-8"))
        zf.writestr(f"{TEST_PREFIX}doc-b.ann", DOC_B_ANN.encode("utf-8"))
        zf.writestr(f"{TEST_PREFIX}doc-a.txt", DOC_A_TEXT.encode("utf-8"))
        zf.writestr(f"{TEST_PREFIX}doc-a.ann", DOC_A_ANN.encode("utf-8"))
        # Other splits must be ignored.
        zf.writestr("meddocan/train/brat/doc-z.txt", b"otro split\n")
        zf.writestr("meddocan/train/brat/doc-z.ann", b"")
        zf.writestr("meddocan/dev/brat/doc-y.txt", b"otro split\n")
        zf.writestr("meddocan/dev/brat/doc-y.ann", b"")


def test_load_test_returns_the_test_documents_sorted_by_name(tmp_path: Path) -> None:
    zip_path = tmp_path / "meddocan.zip"
    _build_zip(zip_path)
    docs = load_test(zip_path)
    assert docs == [
        Document(
            name="doc-a",
            text=DOC_A_TEXT,
            annotations=(
                A(10, 23, "NOMBRE_SUJETO_ASISTENCIA"),
                A(30, 39, "NUMERO_TELEFONO"),
            ),
        ),
        Document(
            name="doc-b",
            text=DOC_B_TEXT,
            annotations=(A(20, 33, "CORREO_ELECTRONICO"),),
        ),
    ]


def test_load_test_keeps_windows_line_endings(tmp_path: Path) -> None:
    zip_path = tmp_path / "meddocan.zip"
    _build_zip(zip_path)
    doc_b = load_test(zip_path)[1]
    assert "\r\n" in doc_b.text
    assert doc_b.text == DOC_B_TEXT


# --- mapping ---------------------------------------------------------------------------


def test_meddocan_has_29_documented_types() -> None:
    assert len(MEDDOCAN_LABELS) == 29
    assert len(set(MEDDOCAN_LABELS)) == 29


def test_every_meddocan_type_has_a_mapping() -> None:
    missing = [label for label in MEDDOCAN_LABELS if label not in MEDDOCAN_TO_ANTIFAZ]
    assert missing == []


def test_mapping_has_no_types_outside_meddocan() -> None:
    assert set(MEDDOCAN_TO_ANTIFAZ) == set(MEDDOCAN_LABELS)


# --- download --------------------------------------------------------------------------


def _no_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("download() must not touch the network here")


def test_download_reuses_a_valid_cached_file_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    data = b"zip inventado ya descargado\n"
    cached = tmp_path / "meddocan.zip"
    cached.write_bytes(data)
    md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()

    sha256 = hashlib.sha256(data).hexdigest()

    path = download(tmp_path, md5=md5, size=len(data), sha256=sha256)

    assert path == cached
    assert cached.read_bytes() == data


def test_download_raises_on_an_invalid_cached_file_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    (tmp_path / "meddocan.zip").write_bytes(b"zip corrupto\n")
    with pytest.raises(ValueError):
        download(tmp_path)


def test_cache_dir_is_inside_evals_datasets() -> None:
    assert meddocan.CACHE_DIR.parts[-3:] == ("evals", "datasets", ".cache")


# Added with the reviews of PR 3a.
class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._done = False

    def read(self, size: int = -1) -> bytes:
        if self._done:
            return b""
        self._done = True
        return self._data

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_download_keeps_a_verified_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"invented zip bytes"
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse(data))
    path = download(
        tmp_path,
        md5=hashlib.md5(data, usedforsecurity=False).hexdigest(),
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    assert path.read_bytes() == data
    assert not (tmp_path / "meddocan.part").exists()


def test_a_bad_download_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse(b"wrong"))
    with pytest.raises(ValueError):
        download(tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("line", ["T1\tCALLE 0\tabc", "T1\tCALLE x 3\tabc", "T1 CALLE 0 3 abc"])
def test_a_malformed_ann_line_gives_its_number_without_the_value(line: str) -> None:
    with pytest.raises(ValueError, match="line 2") as error:
        parse_brat("abc def", "#1\tnote\n" + line)
    assert "abc" not in str(error.value)
