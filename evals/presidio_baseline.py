"""Presidio baseline of Antifaz-Bench (issue 13): Presidio's analyzer, set up for Spanish.

Only for the bench. presidio-analyzer, spaCy and the spaCy model come from the `bench` dependency
group (`make bench PRESIDIO=1`): never shipped and not installed in CI. Everything that touches
them is imported inside build_analyzer(), so this module and its tests (with a fake analyzer)
work without them.

The setup aims at Presidio's best reasonable Spanish configuration, not a strawman:
- language "es" everywhere: one NLP engine for "es" only, a registry for "es" only and an
  analyzer for "es" only. Presidio's default registry is English and, asked in another language,
  it can end up with no recognizer for it without saying so (Presidio discussion #1533): here
  check_spanish() fails at start if any piece is not Spanish or a recognizer is missing.
- recognizers: every one Presidio 2.2.364 enables by default (default_recognizers.yaml) that
  works in any language or in Spanish: credit card (with the Spanish context words of that same
  file), crypto wallet, date, email, IBAN, IP, MAC address, phone (region ES) and URL; the
  Spanish NIF and NIE; the Spanish passport, which comes disabled there and is enabled here; and
  the NER of the NLP engine (PERSON, LOCATION, ORGANIZATION). Recognizers of other countries
  and English-only ones are left out.
- NLP engine: spaCy with xx_ent_wiki_sm 3.8.0 (MIT, multilingual, trained on WikiNER). The
  Spanish spaCy models es_core_news_* are GPL-3.0, so they are not used (ADR-0003). The NER
  settings are those of Presidio's own spacy_multilingual.yaml (PER, LOC and ORG; ORG with a
  lower score), plus MISC ignored, as this model has that label.
- score threshold 0 (Presidio's default): every result counts.
- tldextract (which Presidio's email recognizer uses) reads its bundled public-suffix snapshot
  instead of downloading the list, so a run does not depend on the network.
"""

import tomllib
from collections.abc import Callable, Iterable, Mapping
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

from antifaz.detect.types import EntityType
from evals.metrics import Annotation

E = EntityType
LANGUAGE = "es"
MODEL = "xx_ent_wiki_sm"
MODEL_PACKAGE = "xx-ent-wiki-sm"
MODEL_META = {"lang": "xx", "name": "ent_wiki_sm", "version": "3.8.0"}
PACKAGES = ("presidio-analyzer", "spacy", MODEL_PACKAGE)
LOCK = Path(__file__).resolve().parents[1] / "uv.lock"

NLP_CONFIGURATION: dict[str, Any] = {
    "nlp_engine_name": "spacy",
    "models": [{"lang_code": LANGUAGE, "model_name": MODEL}],
    "ner_model_configuration": {
        "model_to_presidio_entity_mapping": {
            "PER": "PERSON",
            "LOC": "LOCATION",
            "ORG": "ORGANIZATION",
        },
        "low_confidence_score_multiplier": 0.4,
        "low_score_entity_names": ["ORG", "ORGANIZATION"],
        "labels_to_ignore": ["MISC"],
    },
}
PHONE_REGIONS = ("ES",)
# Presidio's phone context words are English; these are their Spanish equivalents. Context only
# raises the score, and with threshold 0 it does not change what is found.
PHONE_CONTEXT = ("teléfono", "telefono", "tel", "móvil", "movil", "fax", "contacto", "llamar")
# The Spanish context of CreditCardRecognizer in Presidio's own default_recognizers.yaml.
CARD_CONTEXT = (
    "tarjeta",
    "credito",
    "visa",
    "mastercard",
    "cc",
    "amex",
    "discover",
    "jcb",
    "diners",
    "maestro",
    "instapayment",
)
RECOGNIZERS = (
    "CreditCardRecognizer",
    "CryptoRecognizer",
    "DateRecognizer",
    "EmailRecognizer",
    "IbanRecognizer",
    "IpRecognizer",
    "MacAddressRecognizer",
    "PhoneRecognizer",
    "UrlRecognizer",
    "EsNifRecognizer",
    "EsNieRecognizer",
    "EsPassportRecognizer",
    "SpacyRecognizer",
)
SCORE_THRESHOLD = 0.0

# Every Presidio type this setup can return -> the Antifaz type it is compared with, or None
# when Antifaz has no equivalent (it still masks text, so it still counts for leaks). Presidio's
# ES_NIF is the DNI with its letter (8 digits + letter): Antifaz calls that ES_DNI.
PRESIDIO_TO_ANTIFAZ: dict[str, EntityType | None] = {
    "EMAIL_ADDRESS": E.EMAIL,
    "PHONE_NUMBER": E.PHONE,
    "IBAN_CODE": E.IBAN,
    "CREDIT_CARD": E.CREDIT_CARD,
    "IP_ADDRESS": E.IP,
    "ES_NIF": E.ES_DNI,
    "ES_NIE": E.ES_NIE,
    "ES_PASSPORT": E.ES_PASSPORT,
    "PERSON": E.PERSON,
    "LOCATION": None,
    "ORGANIZATION": None,
    # DATE_TIME is any date; Antifaz only looks for dates of birth (DATE_OF_BIRTH, with context).
    "DATE_TIME": None,
    "URL": None,
    "CRYPTO": None,
    "MAC_ADDRESS": None,
}
REQUIRED_ENTITIES = frozenset(PRESIDIO_TO_ANTIFAZ)
# Types both tools cover. Which of them a dataset can measure depends on what it annotates.
BOTH_COVER: tuple[EntityType, ...] = tuple(t for t in PRESIDIO_TO_ANTIFAZ.values() if t)


class PresidioSetupError(RuntimeError):
    """Presidio is not set up as declared (not Spanish, a recognizer missing, another model)."""


class Result(Protocol):
    """What the comparison reads from a Presidio RecognizerResult."""

    @property
    def entity_type(self) -> str: ...

    @property
    def start(self) -> int: ...

    @property
    def end(self) -> int: ...


class Analyzer(Protocol):
    """The part of Presidio's AnalyzerEngine the bench uses."""

    def analyze(self, text: str, language: str) -> Iterable[Result]: ...


def to_annotations(results: Iterable[Result]) -> list[Annotation]:
    """Presidio results as Annotations: the Antifaz type when there is one, else Presidio's own
    name. A type outside PRESIDIO_TO_ANTIFAZ is an error, never a silent new label."""
    annotations = []
    for result in results:
        if result.entity_type not in PRESIDIO_TO_ANTIFAZ:
            raise PresidioSetupError(f"unexpected Presidio type: {result.entity_type}")
        entity_type = PRESIDIO_TO_ANTIFAZ[result.entity_type]
        label = entity_type.value if entity_type else result.entity_type
        annotations.append(Annotation(result.start, result.end, label))
    return annotations


def detector(analyzer: Analyzer) -> Callable[[str], list[Annotation]]:
    """A detect() for evals.run.evaluate: the analyzer, always in Spanish."""

    def detect(text: str) -> list[Annotation]:
        return to_annotations(analyzer.analyze(text=text, language=LANGUAGE))

    return detect


def check_spanish(analyzer: Any) -> None:
    """Fail loudly unless the analyzer, its NLP engine and every recognizer are Spanish only and
    every type of PRESIDIO_TO_ANTIFAZ has a Spanish recognizer."""
    if list(analyzer.supported_languages) != [LANGUAGE]:
        raise PresidioSetupError(
            f"the analyzer is not Spanish only: {analyzer.supported_languages}"
        )
    languages = list(analyzer.nlp_engine.get_supported_languages())
    if languages != [LANGUAGE]:
        raise PresidioSetupError(f"the NLP engine is not Spanish only: {languages}")
    others = sorted({r.supported_language for r in analyzer.registry.recognizers} - {LANGUAGE})
    if others:
        raise PresidioSetupError(f"recognizers for other languages: {', '.join(others)}")
    found = set(analyzer.get_supported_entities(language=LANGUAGE))
    missing = sorted(REQUIRED_ENTITIES - found)
    if missing:
        raise PresidioSetupError(f"no Spanish recognizer for: {', '.join(missing)}")


def check_model(meta: Mapping[str, Any]) -> None:
    """Fail unless the loaded spaCy model is the pinned one (MODEL_META)."""
    seen = {key: meta.get(key) for key in MODEL_META}
    if seen != MODEL_META:
        raise PresidioSetupError(f"unexpected spaCy model: {seen}, expected {MODEL_META}")


def locked_sha256(lock_text: str, package: str) -> str:
    """SHA-256 of the wheel uv.lock pins for `package` (uv checks it when it installs it)."""
    for entry in tomllib.loads(lock_text).get("package", []):
        if entry.get("name") == package:
            wheels = entry.get("wheels") or []
            if len(wheels) == 1 and str(wheels[0].get("hash", "")).startswith("sha256:"):
                return str(wheels[0]["hash"]).removeprefix("sha256:")
    raise PresidioSetupError(f"{package} has no single pinned wheel in uv.lock")


def versions(lock: Path = LOCK) -> dict[str, str]:  # pragma: no cover - needs the bench group
    """Installed versions of the bench packages and the SHA-256 of the model wheel."""
    found = {name: metadata.version(name) for name in PACKAGES}
    found["model_sha256"] = locked_sha256(lock.read_text(encoding="utf-8"), MODEL_PACKAGE)
    return found


def configuration() -> dict[str, Any]:
    """The setup, as plain JSON for the report."""
    return {
        "language": LANGUAGE,
        "recognizers": list(RECOGNIZERS),
        "phone_regions": list(PHONE_REGIONS),
        "phone_context": list(PHONE_CONTEXT),
        "credit_card_context": list(CARD_CONTEXT),
        "score_threshold": SCORE_THRESHOLD,
        "nlp_configuration": NLP_CONFIGURATION,
        "mapping": {k: v.value if v else None for k, v in PRESIDIO_TO_ANTIFAZ.items()},
        "public_suffix_list": "tldextract's bundled snapshot (no download)",
    }


def build_analyzer() -> Any:  # pragma: no cover - needs the bench group (make bench PRESIDIO=1)
    """Presidio's AnalyzerEngine with the setup above, checked before it is used."""
    import tldextract.tldextract as tld
    from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_analyzer.predefined_recognizers import (
        CreditCardRecognizer,
        CryptoRecognizer,
        DateRecognizer,
        EmailRecognizer,
        EsNieRecognizer,
        EsNifRecognizer,
        EsPassportRecognizer,
        IbanRecognizer,
        IpRecognizer,
        MacAddressRecognizer,
        PhoneRecognizer,
        UrlRecognizer,
    )

    # Offline and reproducible: the public-suffix list shipped with this tldextract version.
    tld.TLD_EXTRACTOR = tld.TLDExtract(suffix_list_urls=(), cache_dir=None)

    nlp_engine = NlpEngineProvider(nlp_configuration=NLP_CONFIGURATION).create_engine()
    check_model(nlp_engine.nlp[LANGUAGE].meta)
    registry = RecognizerRegistry(supported_languages=[LANGUAGE])
    for recognizer in (
        CryptoRecognizer(supported_language=LANGUAGE),
        DateRecognizer(supported_language=LANGUAGE),
        MacAddressRecognizer(supported_language=LANGUAGE),
        UrlRecognizer(supported_language=LANGUAGE),
        EmailRecognizer(supported_language=LANGUAGE),
        PhoneRecognizer(
            supported_language=LANGUAGE,
            supported_regions=PHONE_REGIONS,
            context=list(PHONE_CONTEXT),
        ),
        IbanRecognizer(supported_language=LANGUAGE),
        CreditCardRecognizer(supported_language=LANGUAGE, context=list(CARD_CONTEXT)),
        IpRecognizer(supported_language=LANGUAGE),
        EsNifRecognizer(supported_language=LANGUAGE),
        EsNieRecognizer(supported_language=LANGUAGE),
        EsPassportRecognizer(supported_language=LANGUAGE),
    ):
        registry.add_recognizer(recognizer)
    registry.add_nlp_recognizer(nlp_engine)  # SpacyRecognizer for "es" (the engine's language)
    analyzer = AnalyzerEngine(
        registry=registry,
        nlp_engine=nlp_engine,
        supported_languages=[LANGUAGE],
        default_score_threshold=SCORE_THRESHOLD,
    )
    check_spanish(analyzer)
    names = sorted({type(r).__name__ for r in analyzer.registry.recognizers})
    if names != sorted(RECOGNIZERS):
        raise PresidioSetupError(f"unexpected recognizers: {names}")
    return analyzer
