"""The NER from the settings, or a refusal to start (ADR-0016).

With ANTIFAZ_NER_ENABLED=true the gateway never starts "without NER" in silence: the model
directory must be set, hold exactly the files of the manifest (sizes and SHA-256) and the
backend must be installed (the `antifaz[ner]` extra). Otherwise UnsafeConfigError,
naming the variable and the reason (a model file name at most, never a key or a text).
"""

import importlib.util
from collections.abc import Mapping
from pathlib import Path

from antifaz.config import Settings, UnsafeConfigError
from antifaz.detect.ner.backend import split_factory
from antifaz.detect.ner.cache import SpanCache
from antifaz.detect.ner.engine import NerDetector
from antifaz.detect.ner.manifest import (
    DEFAULT_MANIFEST,
    Manifest,
    ModelMismatchError,
    load_manifest,
    verify_model_dir,
)
from antifaz.detect.ner.pool import NerPool

GLINER_FACTORY = "antifaz.detect.ner.gliner:create"
# Third-party packages a backend needs besides its own module (the `ner` extra for GLiNER).
BACKEND_REQUIRES: dict[str, tuple[str, ...]] = {
    GLINER_FACTORY: ("gliner", "torch", "transformers"),
}


def _verified(directory: Path, manifest_path: Path) -> Manifest:
    reason = ""
    try:
        manifest = load_manifest(manifest_path)
        verify_model_dir(directory, manifest)
    except ModelMismatchError as error:
        reason = str(error)  # fixed texts: a model file name at most
    if reason:  # raised outside the except block: nothing is chained
        raise UnsafeConfigError(
            f"ANTIFAZ_NER_MODEL_DIR does not match the model manifest: {reason}"
        )
    return manifest


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:  # a parent package is missing
        return False


def ner_from_settings(
    settings: Settings,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    factory: str = GLINER_FACTORY,
    options: Mapping[str, object] | None = None,
) -> NerDetector | None:
    """The NER detector the settings ask for (not started yet), None if the NER is off."""
    if not settings.ner_enabled:
        return None
    if settings.ner_model_dir is None:
        raise UnsafeConfigError(
            "ANTIFAZ_NER_MODEL_DIR is not set but ANTIFAZ_NER_ENABLED is true: download the "
            "model and point ANTIFAZ_NER_MODEL_DIR to it, or turn the NER off"
        )
    manifest = _verified(settings.ner_model_dir, manifest_path)
    required = (split_factory(factory)[0], *BACKEND_REQUIRES.get(factory, ()))
    if not all(_installed(module) for module in required):
        raise UnsafeConfigError(
            "ANTIFAZ_NER_ENABLED is true but the NER backend is not installed "
            "(install the antifaz[ner] extra)"
        )
    pool = NerPool(
        factory,
        {
            "model_dir": str(settings.ner_model_dir),
            "threads": settings.ner_torch_threads,
            **(options or {}),
        },
        workers=settings.ner_workers,
        timeout=settings.ner_timeout_seconds,
        verify={
            "model_dir": str(settings.ner_model_dir),
            "manifest": str(manifest_path),
            "digest": manifest.digest,
        },
    )
    cache = SpanCache(settings.ner_cache_entries) if settings.ner_cache_entries else None
    return NerDetector(
        pool, threshold=settings.ner_threshold, cache=cache, model_id=manifest.digest
    )
