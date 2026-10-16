"""The docs of the distroless image say what the image really needs (issue 54, ADR-0019).

Debugging an image without a shell means attaching a helper container. The guides must give
the safe variant first (no extra capability), and the one that can read files only with the
minimum (SYS_PTRACE alone, everything else dropped, read-only, no new privileges), with the
helper image pinned by digest and a warning that names what it can read: the environment
(keys) and the process memory (personal data in flight). The threat model holds the honest
limit (anyone with Docker on the host can read the keys) and the ADR keeps its reasons.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GUIDES = [REPO / "docs" / "install.md", REPO / "docs" / "instalacion.md"]
ADR = REPO / "docs" / "adr" / "0019-imagen-distroless.md"
THREAT_MODEL = REPO / "docs" / "security" / "threat-model.md"
UPDATE_CHECKER = (
    "https://github.com/dependabot/dependabot-core/blob/main/docker/lib/dependabot/docker/"
    "update_checker.rb"
)


def _busybox_runs(text: str) -> list[str]:
    return [line for line in text.splitlines() if "docker run" in line and "busybox" in line]


@pytest.mark.parametrize("guide", GUIDES, ids=lambda p: p.name)
def test_every_busybox_helper_is_pinned_by_digest(guide: Path) -> None:
    runs = _busybox_runs(guide.read_text(encoding="utf-8"))
    assert len(runs) >= 2, "a variant without SYS_PTRACE and one with it"
    for line in runs:
        assert re.search(r"busybox:[\w.]+@sha256:[0-9a-f]{64}", line), line


@pytest.mark.parametrize("guide", GUIDES, ids=lambda p: p.name)
def test_the_first_helper_has_no_extra_capability(guide: Path) -> None:
    first, *_ = _busybox_runs(guide.read_text(encoding="utf-8"))
    assert "--cap-add" not in first
    assert "--cap-drop ALL" in first and "--security-opt no-new-privileges" in first


@pytest.mark.parametrize("guide", GUIDES, ids=lambda p: p.name)
def test_the_ptrace_helper_drops_everything_else(guide: Path) -> None:
    runs = [r for r in _busybox_runs(guide.read_text(encoding="utf-8")) if "SYS_PTRACE" in r]
    assert runs, "no SYS_PTRACE variant"
    for line in runs:
        for flag in ("--cap-drop ALL", "--cap-add SYS_PTRACE", "--read-only"):
            assert flag in line, flag
        assert "--security-opt no-new-privileges" in line
        assert line.count("--cap-add") == 1


@pytest.mark.parametrize("guide", GUIDES, ids=lambda p: p.name)
def test_the_warning_names_the_environment_and_the_memory(guide: Path) -> None:
    text = guide.read_text(encoding="utf-8")
    assert "/proc/1/environ" in text
    assert re.search(r"\b(memory|memoria)\b", text)
    # Anyone with Docker on the host can read the keys anyway (threat model).
    assert "docker exec" in text and "threat-model.md" in text


def test_the_threat_model_has_the_image_row_with_its_honest_limit() -> None:
    rows = [
        line
        for line in THREAT_MODEL.read_text(encoding="utf-8").splitlines()
        if line.startswith("| Imagen y contenedor")
    ]
    assert len(rows) == 1
    (row,) = rows
    for control in ("shell", "10001", "solo lectura", "cap_drop", "no-new-privileges"):
        assert control in row, control
    assert "docker exec" in row and "SYS_PTRACE" in row


def test_the_adr_keeps_its_reasons_straight() -> None:
    text = ADR.read_text(encoding="utf-8")
    assert "otra libc" not in text  # Wolfi uses glibc too
    assert "latest" in text and "3.15" in text  # Chainguard's free images: only `latest`
    assert UPDATE_CHECKER in text
    assert "grype-gate.yaml" in text  # a slow distroless rebuild needs a dated exception
    assert "threat-model.md" in text
