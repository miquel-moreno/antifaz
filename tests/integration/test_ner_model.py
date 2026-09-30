"""The real NER model (issue 6b): GLiNER from the downloaded, manifest-checked files.

Marked `ner_model`: skipped unless ANTIFAZ_NER_MODEL_DIR points to the model (`make ner-model`)
and the `ner` extra is installed. CI runs none of this (no torch, no model). Every name and
address is invented.
"""

import json
import os
import random
import re
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from antifaz import guard
from antifaz.api.app import create_app
from antifaz.detect.ner import gliner as backend
from antifaz.detect.ner.chunker import WINDOW_TOKENS, chunk
from antifaz.detect.ner.gliner import MAX_MODEL_TOKENS, GlinerBackend
from antifaz.detect.ner.manifest import load_manifest, verify_model_dir
from tests.conftest import SENTINEL_DNI
from tests.integration import test_proxy_openai as openai_tests
from tests.integration.fakes import GATEWAY_KEY, FakeUpstream

pytestmark = pytest.mark.ner_model

LABELS = ["address", "person"]
OPENAI_AUTH = {"Authorization": f"Bearer {GATEWAY_KEY}"}


def _model_dir() -> Path:
    return Path(os.environ["ANTIFAZ_NER_MODEL_DIR"])


def _block_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every connection or name lookup fails, loopback included, and is recorded."""
    attempts: list[str] = []

    def refuse(*args: Any, **kwargs: Any) -> Any:
        attempts.append(repr(args[:2]))
        raise OSError("network blocked by the test")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    return attempts


@pytest.fixture(scope="module")
def loaded() -> Iterator[tuple[GlinerBackend, list[str]]]:
    """The model, loaded in this process with the network blocked (the pool loads it the same
    way in its workers)."""
    pytest.importorskip("gliner")
    verify_model_dir(_model_dir(), load_manifest())
    with pytest.MonkeyPatch.context() as monkeypatch:
        for name in backend.OFFLINE_ENV:
            monkeypatch.delenv(name, raising=False)  # create() must set them itself
        attempts = _block_network(monkeypatch)
        model = backend.create(str(_model_dir()), threads=4)
        model.predict(["La doctora Carmen Prueba López atendió al paciente."], LABELS, 0.5)
        offline = {name: os.environ.get(name) for name in backend.OFFLINE_ENV}
    # The network comes back for the other tests (the event loop of TestClient uses loopback).
    assert set(offline.values()) == {"1"}
    yield model, attempts


@pytest.fixture
def model(loaded: tuple[GlinerBackend, list[str]]) -> GlinerBackend:
    return loaded[0]


def _found(model: GlinerBackend, text: str, threshold: float = 0.5) -> dict[str, list[str]]:
    (entities,) = model.predict([text], LABELS, threshold)
    out: dict[str, list[str]] = {}
    for start, end, label, _ in entities:
        assert isinstance(start, int) and isinstance(end, int) and isinstance(label, str)
        out.setdefault(label, []).append(text[start:end])
    return out


def test_the_model_loads_and_predicts_without_any_network(
    loaded: tuple[GlinerBackend, list[str]],
) -> None:
    """Loading and a first prediction ran with every socket refused (see the fixture): no
    attempt was even made."""
    _, attempts = loaded
    assert attempts == []


@pytest.mark.parametrize(
    ("text", "person", "address"),
    [
        (
            "El paciente Jordi Inventat Puig, con domicilio en la Calle Ficticia 12, 3º B, "
            "acudió a consulta.",
            "Jordi Inventat Puig",
            "Calle Ficticia 12",
        ),
        (
            "Hola, soy Lucía Ejemplo Martín y vivo en Avenida de los Pruebas 45, Terrassa. "
            "¿Podéis enviarme la factura?",
            "Lucía Ejemplo Martín",
            "Avenida de los Pruebas 45",
        ),
        (
            "Remitente: Marc Inventat Soler. Dirección de envío: Plaza Imaginaria 3, 2º 1ª, "
            "08001 Barcelona.",
            "Marc Inventat Soler",
            "Plaza Imaginaria 3",
        ),
    ],
)
def test_the_real_model_finds_invented_spanish_names_and_addresses(
    model: GlinerBackend, text: str, person: str, address: str
) -> None:
    found = _found(model, text)
    assert any(person in value for value in found.get("person", [])), found
    assert any(address in value for value in found.get("address", [])), found


def _adversarial_texts() -> list[tuple[str, str]]:
    rng = random.Random(6)  # noqa: S311 - fixed seed: the same test texts every run
    digits = " ".join("".join(rng.choice("0123456789") for _ in range(40)) for _ in range(250))
    accents = " ".join(
        "".join(rng.choice("áéíóúàèòïüçñÁÉÍÓÚÑ·ªº") for _ in range(12)) for _ in range(250)
    )
    symbols = "".join(rng.choice("€%&@#¿¡«»—–…‰") for _ in range(600))  # noqa: RUF001
    mixed = " ".join(
        "".join(rng.choice("abcxyzQWZ0123456789áßøæœ") for _ in range(40)) for _ in range(250)
    )
    emoji = " ".join("🙂🧪🏥" * 3 for _ in range(250))
    cjk = " ".join("患者名前住所" * 5 for _ in range(250))
    hyphens = " ".join("-".join("x1" for _ in range(8)) for _ in range(250))
    return [
        ("long numbers", digits),
        ("accented words", accents),
        ("symbols", symbols),
        ("mixed", mixed),
        ("emoji", emoji),
        ("cjk", cjk),
        ("hyphenated", hyphens),
    ]


@pytest.mark.parametrize(("kind", "text"), _adversarial_texts(), ids=lambda v: str(v)[:14])
def test_no_call_to_the_model_passes_its_token_limit(
    model: GlinerBackend, kind: str, text: str
) -> None:
    """Windows of 200 of our tokens can be thousands of model tokens (subwords); the backend
    cuts them again so that no call passes MAX_MODEL_TOKENS, prompt included."""
    processor = _processor()
    windows = chunk(text)
    assert windows and all(len(w.text) > 0 for w in windows)
    worst = 0
    for window in windows:
        for piece in model.windows(window.text, LABELS):
            words = [word for word, _, _ in processor.words_splitter(piece.text)]
            tokenized = processor.tokenize_inputs([words], LABELS)
            worst = max(worst, int(tokenized["input_ids"].shape[1]))
    assert 0 < worst <= MAX_MODEL_TOKENS, kind


def test_our_windows_of_plain_spanish_fit_the_model_in_one_piece(model: GlinerBackend) -> None:
    """Ordinary text: a window of 200 of our tokens is one call (no cost from re-cutting)."""
    sentence = (
        "La paciente acudió a la consulta del centro de salud para una revisión rutinaria, "
        "y el médico le recomendó volver dentro de tres meses. "
    )
    text = sentence * 40
    windows = chunk(text)
    assert len(windows) > 1 and all(len(model.windows(w.text, LABELS)) == 1 for w in windows)
    assert WINDOW_TOKENS == 200


def test_a_name_after_many_model_tokens_is_still_found(model: GlinerBackend) -> None:
    """Without re-cutting, a name after 190 long numbers (2,330 subwords) was not seen."""
    rng = random.Random(1)  # noqa: S311 - fixed seed for test data, not security
    numbers = " ".join("".join(rng.choice("0123456789") for _ in range(40)) for _ in range(190))
    text = f"{numbers} El paciente Jordi Inventat Puig ingresó ayer."
    (window,) = chunk(text)  # one of our windows...
    assert len(model.windows(window.text, LABELS)) > 1  # ...read in several pieces
    assert "Jordi Inventat Puig" in _found(model, text).get("person", [])


def _processor() -> Any:
    """GLiNER's own data processor (tokenizer and word splitter), loaded offline."""
    from gliner import GLiNER

    config = backend.model_config(_model_dir())
    model_class = GLiNER._get_gliner_class(GLiNER._config_from_dict(dict(config)))
    return model_class.load_from_config(
        config, load_tokenizer=True, backbone_from_pretrained=False, local_files_only=True
    ).data_processor


# End to end: the gateway with the real model in its worker process and a fake provider.


def test_end_to_end_names_never_reach_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("gliner")
    monkeypatch.setattr(guard, "check", lambda payload, vault: None)  # ONLY here: invariant 2
    upstream = FakeUpstream(openai_tests.echo)
    settings = openai_tests._settings(
        ner_enabled=True, ner_model_dir=_model_dir(), ner_timeout_seconds=120, ner_workers=1
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    text = (
        f"Cliente: Jordi Inventat Puig (DNI {SENTINEL_DNI}), Calle Ficticia 12, 3º B. "
        "Escribe una respuesta amable para Jordi Inventat Puig."
    )
    with TestClient(create_app(settings, http_client=http)) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": text}]},
            headers=OPENAI_AUTH,
        )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == text  # restored
    (request,) = upstream.requests
    sent = json.dumps(json.loads(request.content), ensure_ascii=False)
    for value in ("Jordi", "Inventat", "Puig", "Ficticia", SENTINEL_DNI):
        assert value not in sent
    assert re.search(r"\[\[PERSON_1\]\]", sent) and "[[ADDRESS_1]]" in sent


def test_scoring_once_matches_running_each_threshold_with_the_real_model(
    model: GlinerBackend,
) -> None:
    """The bench's ScoreCache (evals/run.py) is exact with GLiNER, not only with the fake."""
    from evals.run import ScoreCache

    from antifaz.detect.ner.engine import NerDetector
    from antifaz.detect.scan import Scanner
    from tests.nerfakes import InProcess

    texts = [
        "La doctora Carmen Prueba López y el enfermero Jordi Inventat Puig atendieron a "
        "Lucía Ejemplo Martín, que vive en la Calle Ficticia 12, 3º B, de Pallejà.",
        "Remitente: Marc Inventat Soler. Plaza Imaginaria 3, 2º 1ª, 08001 Barcelona. "
        "Su madre, Ana, y su hermano Pau llamaron al Hospital Inventado.",
    ]
    scored = ScoreCache(InProcess(model), 0.3)  # type: ignore[arg-type]
    for threshold in (0.3, 0.4, 0.5, 0.6):
        once = Scanner(NerDetector(scored, threshold=threshold))
        every = Scanner(NerDetector(InProcess(model), threshold=threshold))  # type: ignore[arg-type]
        for text in texts:
            assert once(text) == every(text), threshold
