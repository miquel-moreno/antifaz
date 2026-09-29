"""MEDDOCAN (CC-BY-4.0): Spanish clinical cases with personal data annotated in brat.

Only the test split is used. The zip is downloaded once to CACHE_DIR (git-ignored) and
checked by size and MD5 before use.
"""

import hashlib
import shutil
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from antifaz.detect.types import EntityType
from evals.metrics import Annotation

URL = "https://zenodo.org/records/4279323/files/meddocan.zip"
MD5 = "6a09eb975580fdf56bc7041eadc9c921"
SIZE = 11738792
CACHE_DIR = Path(__file__).resolve().parents[2] / "evals" / "datasets" / ".cache"
TEST_PREFIX = "meddocan/test/brat/"

E = EntityType

# Every MEDDOCAN label -> the Antifaz type that should catch it, or None when Antifaz does
# not cover it yet (it still counts for leaks).
MEDDOCAN_TO_ANTIFAZ: dict[str, EntityType | None] = {
    "CORREO_ELECTRONICO": E.EMAIL,
    "NUMERO_TELEFONO": E.PHONE,
    "NUMERO_FAX": E.PHONE,
    "CALLE": E.ADDRESS,
    "ID_ASEGURAMIENTO": E.ES_NSS,
    "ID_SUJETO_ASISTENCIA": None,
    "ID_TITULACION_PERSONAL_SANITARIO": None,
    "ID_CONTACTO_ASISTENCIAL": None,
    "FECHAS": None,
    "NOMBRE_SUJETO_ASISTENCIA": None,
    "NOMBRE_PERSONAL_SANITARIO": None,
    "FAMILIARES_SUJETO_ASISTENCIA": None,
    "TERRITORIO": None,
    "PAIS": None,
    "HOSPITAL": None,
    "INSTITUCION": None,
    "CENTRO_SALUD": None,
    "EDAD_SUJETO_ASISTENCIA": None,
    "SEXO_SUJETO_ASISTENCIA": None,
    "PROFESION": None,
    "OTROS_SUJETO_ASISTENCIA": None,
    "ID_EMPLEO_PERSONA": None,
    "IDENTIF_VEHICULOS_NRSERIE_PRODUCTOS": None,
    "IDENTIF_DISPOSITIVOS_NRSERIE": None,
    "NUMERO_BENEF_PLAN_SALUD": None,
    "URL_WEB": None,
    "DIREC_PROT_INTERNET": None,
    "OTRO_NUMERO_IDENTIF": None,
    # 29th type of the MEDDOCAN annotation guidelines (biometric identifiers). It was not in
    # the plan's list of 28; to be confirmed against the guidelines when the data is read.
    "IDENTIF_BIOMETRICOS": None,
}


@dataclass(frozen=True)
class Document:
    """One clinical case: name (file stem, e.g. "S0004-06142005000500011-1"), text and
    gold annotations."""

    name: str
    text: str
    annotations: tuple[Annotation, ...]


def parse_brat(text: str, ann: str) -> tuple[Annotation, ...]:
    """Annotations of a brat .ann file. Only T lines are read (`#` notes, relations, etc.
    are ignored); a discontinuous span gives one Annotation per fragment. Raises
    ValueError, without the annotated value in the message, if the offsets do not match
    the text (fragments of a discontinuous span are compared joined by one space)."""
    annotations: list[Annotation] = []
    for number, line in enumerate(ann.splitlines(), start=1):
        if not line.startswith("T"):
            continue
        _, spec, annotated = line.split("\t", 2)
        label, _, ranges = spec.partition(" ")
        fragments = []
        for part in ranges.split(";"):
            start, end = (int(offset) for offset in part.split())
            fragments.append(Annotation(start, end, label))
        if " ".join(text[f.start : f.end] for f in fragments) != annotated:
            # Never include the value: it may be personal data (synthetic here, but still).
            raise ValueError(f"brat offsets do not match the text (line {number}, {label})")
        annotations.extend(fragments)
    return tuple(annotations)


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(path: Path, md5: str = MD5, size: int = SIZE) -> None:
    """Raise ValueError if the file size or MD5 differ from the expected ones."""
    if path.stat().st_size != size:
        raise ValueError(f"{path.name}: unexpected size")
    if _md5(path) != md5:
        raise ValueError(f"{path.name}: unexpected MD5")


def load_test(zip_path: Path) -> list[Document]:
    """Documents of the test split (every .txt under TEST_PREFIX with its .ann), sorted by
    name. Files are read as bytes and decoded as UTF-8: "\\r\\n" is kept as is so the brat
    offsets stay valid."""
    documents = []
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        for name in sorted(n for n in names if n.startswith(TEST_PREFIX) and n.endswith(".txt")):
            text = archive.read(name).decode("utf-8")
            ann_name = name[: -len(".txt")] + ".ann"
            ann = archive.read(ann_name).decode("utf-8") if ann_name in names else ""
            stem = name[len(TEST_PREFIX) : -len(".txt")]
            documents.append(Document(stem, text, parse_brat(text, ann)))
    return documents


def download(dest_dir: Path = CACHE_DIR, *, md5: str = MD5, size: int = SIZE) -> Path:
    """Path to dest_dir / "meddocan.zip", downloading it (with urllib.request.urlopen)
    only when it is not there yet. An existing file is verified and reused without any
    network access; if it fails verification, ValueError is raised."""
    path = dest_dir / "meddocan.zip"
    if path.exists():
        verify(path, md5, size)
        return path
    dest_dir.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    # URL is a fixed https address (a constant above), never user input.
    with urllib.request.urlopen(URL, timeout=60) as response, partial.open("wb") as file:
        shutil.copyfileobj(response, file)
    verify(partial, md5, size)
    partial.replace(path)
    return path
