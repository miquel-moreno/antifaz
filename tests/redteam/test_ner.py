"""Red team of the NER layer (issue 6a, ADR-0016): names that try to reach the provider.

The model is simulated with the dictionary FakeBackend (it finds only the exact names it was
given, with word boundaries), sometimes wrapped so it "misses" a name, like a real model can.
Tests marked xfail(strict=True) are findings: they turn red (XPASS) once fixed. All names,
phones and emails are invented.
"""

import contextlib
import json
import time
import unicodedata
from collections.abc import Sequence
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from antifaz import DetectorFailed, guard, mask
from antifaz.api.app import create_app
from antifaz.config import UnsafeConfigError
from antifaz.detect.ner.cache import SpanCache
from antifaz.detect.ner.engine import NerDetector
from antifaz.detect.ner.fake import FakeBackend
from antifaz.detect.ner.pool import NerPool
from antifaz.detect.ner.setup import ner_from_settings
from antifaz.detect.normalize import normalize
from antifaz.detect.scan import Scanner
from antifaz.errors import UnmaskableField
from antifaz.providers.openai_chat import mask_request
from tests.integration import test_proxy_openai as oai
from tests.integration.fakes import FakeUpstream
from tests.nerfakes import ANA, CARMEN, FAKE_FACTORY, JORDI, NAMES, InProcess
from tests.redteam.conftest import OPENAI_AUTH, PHONE, assert_not_sent

# --- helpers -----------------------------------------------------------------------------


def _fold(text: str) -> str:
    """What an attacker hopes survives: the detector's view, no accents, casefold, alnum only."""
    view = normalize(text).text
    decomposed = unicodedata.normalize("NFD", view)
    kept = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return "".join(c for c in kept.casefold() if c.isalnum())


def _leaks(value: str, texts: Sequence[str]) -> bool:
    return any(_fold(value) in _fold(text) for text in texts)


def scanner(names: dict[str, str] | None = None, **options: Any) -> tuple[Scanner, InProcess]:
    predictor = InProcess(FakeBackend(names or dict(NAMES)))
    options.setdefault("cache", SpanCache(100))
    return Scanner(NerDetector(predictor, **options)), predictor


class OnlyFirst(InProcess):
    """A NER that sees the names in the first window it is asked about and misses them later."""

    def predict(self, texts: Sequence[str], labels: Sequence[str], threshold: float) -> Any:
        results = super().predict(texts, labels, threshold)
        return [r if i == 0 and self.calls == 1 else [] for i, r in enumerate(results)]


class Substrings:
    """A NER that marks fixed substrings (no word boundaries): odd spans on purpose."""

    def __init__(self, *values: str) -> None:
        self.values = values

    def start(self) -> None: ...

    def close(self) -> None: ...

    def predict(self, texts: Sequence[str], labels: Sequence[str], threshold: float) -> Any:
        out = []
        for text in texts:
            found = []
            for value in self.values:
                i = text.find(value)
                while i != -1:
                    found.append([i, i + len(value), "person", 0.9])
                    i = text.find(value, i + 1)
            out.append(found)
        return out


class FailsOnce(InProcess):
    def predict(self, texts: Sequence[str], labels: Sequence[str], threshold: float) -> Any:
        if self.calls == 0:
            self.calls += 1
            raise RuntimeError("model crashed")
        return super().predict(texts, labels, threshold)


def _only_first_scanner() -> Scanner:
    return Scanner(NerDetector(OnlyFirst(FakeBackend(dict(NAMES))), batch_chunks=64))


def _masked_and_guarded(texts: list[str], detector: Any) -> tuple[str, ...]:
    """mask() and then the egress guard on the JSON that would leave (raises if it blocks)."""
    result = mask(texts, detector=detector)
    guard.check(json.dumps(list(result.texts), ensure_ascii=False), result.vault)
    return result.texts


# --- 1. windows ------------------------------------------------------------------------------


@pytest.mark.parametrize("position", range(140, 210, 3))
def test_a_name_across_a_window_border_is_masked(position: int) -> None:
    """Un nombre que cae justo en el corte entre dos ventanas del NER se enmascara igual."""
    words = ["palabra"] * 600
    words[position] = CARMEN
    text = " ".join(words)
    detector, _ = scanner()
    (masked,) = mask([text], detector=detector).texts
    assert not _leaks(CARMEN, [masked])


def test_a_name_across_a_border_with_tiny_windows() -> None:
    """Con ventanas mínimas (3 tokens de solape) un nombre de 3 tokens sigue entero en alguna."""
    for position in range(0, 20):
        words = ["x"] * 25
        words[position] = CARMEN
        detector, _ = scanner(window=8, overlap=3)
        (masked,) = mask([" ".join(words)], detector=detector).texts
        assert not _leaks(CARMEN, [masked]), position


def test_long_texts_are_read_whole_not_cut_at_384_words() -> None:
    """Un nombre en la palabra 5000 (GLiNER cortaría en la 384) también se enmascara."""
    text = " ".join(["relleno"] * 5000) + f" {JORDI}"
    detector, predictor = scanner()
    (masked,) = mask([text], detector=detector).texts
    assert not _leaks(JORDI, [masked])
    assert all(len(t.split()) <= 200 for t in predictor.texts)


# --- 2. across messages, variants and propagation ---------------------------------------------


@pytest.mark.parametrize(
    "variant",
    [
        "CARMEN PRUEBA LÓPEZ",
        "carmen prueba lopez",
        "Cármen Prueba López",
        "Carmen Prueba Lo\u0301pez",  # accent written apart
        "Carmen\u200bPrueba\u00adLópez",  # zero-width space and soft hyphen
        "\uff23\uff41\uff52\uff4d\uff45\uff4e Prueba López",  # full-width
        "xxCarmenPruebaLópezyy",  # glued to other letters
        "Carmen Prueba López's",
        "C.a.r.m.e.n Prueba-López",
        "Carmen\nPrueba\tLópez",
    ],
)
def test_a_name_the_ner_missed_in_another_message_is_propagated(variant: str) -> None:
    """El NER ve el nombre en el mensaje 1 y no en el 2 (escrito raro): se enmascara igual."""
    texts = _masked_and_guarded([f"Soy {CARMEN}.", f"Firmado: {variant}"], _only_first_scanner())
    assert not _leaks(CARMEN, texts)


@pytest.mark.parametrize("variant", ["ANA vino", "Ána vino", "vino d'Ana", "(Ana)", "Ana,"])
def test_a_short_name_is_propagated_with_boundaries(variant: str) -> None:
    """Un nombre corto ("Ana") visto en un mensaje se tapa en el otro, en mayúsculas o con tilde."""
    texts = _masked_and_guarded([f"Hola {ANA}", variant], _only_first_scanner())
    assert all("ana" not in _fold(t).replace("person", "") for t in texts)


def test_a_homoglyph_spelling_the_ner_missed_is_propagated() -> None:
    """El NER ve 'Carmen…' en latino en un mensaje y falla el otro, escrito con una 'a' cirílica."""
    variant = "C\u0430rmen Prueba López"  # Cyrillic a
    texts = _masked_and_guarded([f"Soy {CARMEN}.", f"Firmado: {variant}"], _only_first_scanner())
    assert not _leaks(CARMEN, texts)


@pytest.mark.xfail(strict=True, reason="LÍMITE: solo se propaga el valor entero, no sus partes")
def test_the_first_name_alone_is_propagated() -> None:
    """El NER ve 'Carmen Prueba López' y en otro mensaje solo pone 'Carmen': ¿se tapa?"""
    texts = _masked_and_guarded([f"Soy {CARMEN}.", "Saludos, Carmen"], _only_first_scanner())
    assert "carmen" not in _fold(texts[1])


def test_a_name_in_every_message_and_the_system_prompt_never_reaches_the_provider() -> None:
    """Nombre en system, historial, `user` y argumentos de herramienta: nada llega en claro."""
    upstream = FakeUpstream(oai.echo)
    body = {
        "model": "m",
        "user": JORDI,
        "messages": [
            {"role": "system", "content": f"Atiende a {CARMEN}"},
            {"role": "user", "content": f"hola, soy {CARMEN.upper()}"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "f", "arguments": json.dumps({"cliente": JORDI})},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"n": {"x": JORDI}})},
            {"role": "user", "content": "ok"},
        ],
    }
    ner = NerDetector(InProcess(FakeBackend(dict(NAMES))), cache=SpanCache(10))
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(oai._settings(), http_client=http, detector=Scanner(ner))
    with TestClient(app) as client:
        assert (
            client.post("/v1/chat/completions", json=body, headers=OPENAI_AUTH).status_code == 200
        )
    assert_not_sent(upstream, CARMEN, JORDI)


# --- 3. JSON fields ----------------------------------------------------------------------------


def test_a_name_in_an_object_key_blocks_the_request() -> None:
    """Un nombre como CLAVE de un objeto no se puede enmascarar: la petición se bloquea."""
    body = {"model": "m", "messages": [{"role": "user", "content": "hola"}], "meta": {CARMEN: 1}}
    with pytest.raises(UnmaskableField):
        mask_request(body, detector=scanner()[0])


@pytest.mark.xfail(strict=True, reason="LÍMITE: cada cadena se lee por separado")
def test_a_name_split_across_two_json_fields() -> None:
    """'Carmen' en un campo y 'Prueba López' en otro: el NER no ve el nombre entero."""
    body = {
        "model": "m",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Carmen"}]},
            {"role": "user", "content": [{"type": "text", "text": "Prueba López"}]},
        ],
    }
    masked, _ = mask_request(body, detector=scanner()[0])
    assert "Carmen" not in json.dumps(masked, ensure_ascii=False)


# --- 4. invisible characters and homoglyphs inside the name the NER reads ----------------------


@pytest.mark.parametrize(
    "written",
    [
        "Car\u200bmen Prueba López",
        "Car\u00admen Prueba López",
        "C\u0430rmen Prueba López",  # Cyrillic a
        "Carm\u0435n Prueba López",  # Cyrillic e
        "\uff23armen Prueba López",
        "\u2060Carmen Prueba López\u2060",
    ],
)
def test_the_ner_reads_the_normalised_view_and_masks_the_original(written: str) -> None:
    """Invisibles y letras de otros alfabetos dentro del nombre: el NER lo ve y se tapa entero."""
    (masked,) = _masked_and_guarded([f"Soy {written}, hola"], scanner()[0])
    assert "[[PERSON_1]]" in masked  # invisible chars around the value may stay: harmless
    assert masked.endswith(", hola")
    assert not _leaks(CARMEN, [masked])


# --- 5. partial overlaps between the NER and email / phone -------------------------------------


@pytest.mark.parametrize(
    ("text", "ner"),
    [
        (f"Jordi Inventat Puig {PHONE}", "Inventat Puig 6123"),
        ("Jordi Inventat Puig <jordi.inventat@ejemplo.es>", "Puig <jordi.inv"),
        ("escribe a jordi.inventat@ejemplo.es Jordi", "es Jordi"),
        (f"tel {PHONE} Inventat", "45678 Inventat"),
    ],
)
def test_a_ner_span_partly_over_an_email_or_phone_leaves_nothing_in_clear(
    text: str, ner: str
) -> None:
    """El NER marca un trozo que pisa medio email o medio teléfono: no queda nada en claro."""
    detector = Scanner(NerDetector(Substrings(ner)))
    (masked,) = _masked_and_guarded([text], detector)
    for value in (PHONE, "jordi.inventat@ejemplo.es", ner):
        if value in text:
            assert not _leaks(value, [masked]), value
    assert not any(c.isdigit() for c in masked.replace("_1]]", "").replace("_2]]", ""))


# --- 6. cache: poisoning and cross-request leaks -------------------------------------------------


def test_the_cache_keeps_no_text_and_does_not_mix_requests() -> None:
    """La caché guarda solo posiciones; la petición B no ve valores ni marcadores de la A."""
    cache = SpanCache(100)
    detector, _ = scanner(cache=cache)
    a = mask([f"Soy {CARMEN}"], detector=detector)
    b = mask([f"Soy {JORDI}", "[[PERSON_1]]"], detector=detector)
    assert [v for _, v in b.vault._hidden_values()] == [JORDI]
    assert b.texts[1] == "[[PERSON_1]]" or CARMEN not in b.texts[1]
    for key, spans in cache._entries.items():
        assert isinstance(key, bytes) and len(key) == 32
        assert CARMEN.encode() not in key and JORDI.encode() not in key
        assert all(type(s.start) is int for s in spans)
    assert CARMEN not in repr(cache) and a.vault is not b.vault


def test_the_same_view_from_two_spellings_shares_the_cache_but_maps_back_right() -> None:
    """Dos textos con la misma vista normalizada (uno con invisibles) usan la misma entrada de
    caché, y cada uno se tapa sobre SUS caracteres originales."""
    detector, predictor = scanner()
    plain = f"Soy {CARMEN}, hola"
    hidden = "Soy Car\u200bmen\u200b Prueba López, hola"
    (first,) = mask([plain], detector=detector).texts
    (second,) = mask([hidden], detector=detector).texts
    assert predictor.calls == 1  # second one came from the cache
    assert first == second == "Soy [[PERSON_1]], hola"


def test_a_failed_ner_call_does_not_poison_the_cache() -> None:
    """Si el NER falla, no se guarda 'sin nombres' en la caché: el siguiente intento lo tapa."""
    cache = SpanCache(100)
    predictor = FailsOnce(FakeBackend(dict(NAMES)))
    detector = Scanner(NerDetector(predictor, cache=cache))
    with pytest.raises(DetectorFailed):
        mask([f"Soy {CARMEN}"], detector=detector)
    assert len(cache) == 0
    (masked,) = mask([f"Soy {CARMEN}"], detector=detector).texts
    assert masked == "Soy [[PERSON_1]]"


def test_another_threshold_or_model_misses_the_cache() -> None:
    """Un resultado guardado con otro umbral o modelo no se reutiliza (claves distintas)."""
    cache = SpanCache(100)
    low = NerDetector(InProcess(FakeBackend(dict(NAMES), score=0.6)), cache=cache, threshold=0.9)
    assert low.find_many([CARMEN]) == [[]]
    high = NerDetector(InProcess(FakeBackend(dict(NAMES), score=0.6)), cache=cache, threshold=0.5)
    assert high.find_many([CARMEN]) != [[]]
    other = NerDetector(InProcess(FakeBackend({})), cache=cache, threshold=0.5, model_id="b")
    assert other.find_many([CARMEN]) == [[]]


# --- 7. the pool: not started, closed, crash, timeout, stderr -----------------------------------


def _pool(**options: Any) -> NerPool:
    backend = {"names": dict(NAMES), "triggers": True, **options.pop("backend", {})}
    return NerPool(FAKE_FACTORY, backend, workers=1, **{"timeout": 2.0, **options})


def test_a_pool_that_never_started_blocks() -> None:
    """Si el pool de NER no arrancó, la petición se bloquea (nunca sale sin NER)."""
    detector = Scanner(NerDetector(_pool()))
    with pytest.raises(DetectorFailed):
        mask([f"Soy {CARMEN}"], detector=detector)


def test_workers_never_write_names_and_failures_block(capfd: pytest.CaptureFixture[str]) -> None:
    """El proceso NER imprime, lanza errores, se cuelga y muere con el nombre: nada sale por
    stdout/stderr y cada petición se bloquea."""
    pool = _pool(timeout=1.5, backend={"sleep_seconds": 30})
    detector = Scanner(NerDetector(pool))
    pool.start()
    try:
        for trigger in ("FAKE_PRINT FAKE_RAISE", "FAKE_SLEEP", "FAKE_CRASH", "FAKE_MALFORMED"):
            with pytest.raises(DetectorFailed):
                mask([f"{trigger} {CARMEN} {PHONE}"], detector=detector)
        (ok,) = mask([f"FAKE_PRINT {CARMEN}"], detector=detector).texts
        assert CARMEN not in ok
    finally:
        pool.close()
    out, err = capfd.readouterr()
    for value in (CARMEN, PHONE):
        assert value not in out and value not in err


def test_a_crash_in_a_late_window_blocks_the_whole_text() -> None:
    """El NER muere en la ventana 30 de un texto largo: se bloquea todo, no sale la mitad."""
    pool = _pool()
    detector = Scanner(NerDetector(pool, cache=SpanCache(10)))
    pool.start()
    try:
        text = f"{CARMEN} " + " ".join(["relleno"] * 5000) + " FAKE_CRASH"
        with pytest.raises(DetectorFailed):
            mask([text], detector=detector)
        (after,) = mask([f"Soy {CARMEN}"], detector=detector).texts
        assert after == "Soy [[PERSON_1]]"
    finally:
        pool.close()


@pytest.mark.xfail(strict=True, reason="DoS media: el tiempo máximo es por lote, no por petición")
def test_a_huge_text_is_bounded_by_the_ner_time_limit() -> None:
    """Un texto con muchas ventanas lentas (cada lote justo por debajo del límite) no debería
    retener el NER mucho más que ANTIFAZ_NER_TIMEOUT_SECONDS."""
    timeout = 1.0
    pool = _pool(timeout=timeout, backend={"sleep_seconds": 0.05})
    detector = Scanner(NerDetector(pool))
    pool.start()
    try:
        text = ("FAKE_SLEEP " + "palabra " * 99) * 150  # ~100 windows, 16 per call
        began = time.monotonic()
        with contextlib.suppress(DetectorFailed):
            mask([text], detector=detector)
        elapsed = time.monotonic() - began
    finally:
        pool.close()
    assert elapsed < 2 * timeout


# --- 8. configuration --------------------------------------------------------------------------


def test_ner_enabled_without_a_model_refuses_to_start() -> None:
    """ANTIFAZ_NER_ENABLED=true sin modelo: la pasarela no arranca (nunca 'sin NER' en silencio)."""
    settings = oai._settings(ner_enabled=True)
    with pytest.raises(UnsafeConfigError):
        ner_from_settings(settings)
    with pytest.raises(UnsafeConfigError):
        create_app(settings)


def test_ner_enabled_with_the_placeholder_manifest_refuses_to_start(tmp_path: Any) -> None:
    """Con el manifiesto vacío de serie, cualquier carpeta de modelo se rechaza."""
    (tmp_path / "model.bin").write_bytes(b"not a model")
    with pytest.raises(UnsafeConfigError):
        create_app(oai._settings(ner_enabled=True, ner_model_dir=tmp_path))


def test_a_worker_that_cannot_load_stops_the_startup() -> None:
    """Si el modelo no carga en el proceso NER, la app no arranca (no sirve sin NER)."""
    pool = NerPool(FAKE_FACTORY, {"names": {}, "bogus_option": CARMEN}, workers=1, timeout=2.0)
    http = httpx.AsyncClient(transport=httpx.MockTransport(FakeUpstream(oai.echo)))
    app = create_app(oai._settings(), http_client=http, ner=NerDetector(pool))
    with pytest.raises(Exception) as error, TestClient(app):
        pass
    assert CARMEN not in str(error.value)
