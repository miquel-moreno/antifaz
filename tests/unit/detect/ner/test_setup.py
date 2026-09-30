"""NER settings and startup (ADR-0016): off by default; when on, the model must be exactly the
one in the manifest and the backend installed, or Antifaz refuses to start."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from antifaz.api.app import create_app
from antifaz.config import Settings, UnsafeConfigError
from antifaz.detect.ner.engine import NerDetector
from antifaz.detect.ner.setup import GLINER_FACTORY, ner_from_settings
from tests.nerfakes import CARMEN, FAKE_FACTORY

KEY = "test-gateway-key-not-real-0123456789abcdef"
WEIGHTS = b"synthetic weights for the test, not a model"


def _settings(**values: object) -> Settings:
    base: dict[str, object] = {
        "antifaz_api_key": SecretStr(KEY),
        "allowed_hosts": ["testserver"],
        "_env_file": None,
    }
    return Settings(**{**base, **values})  # type: ignore[arg-type]


@pytest.fixture
def model(tmp_path: Path) -> tuple[Path, Path]:
    """A model directory and a manifest that matches it."""
    directory = tmp_path / "model"
    directory.mkdir()
    (directory / "weights.safetensors").write_bytes(WEIGHTS)
    manifest = tmp_path / "manifest.json"
    entry = {"size": len(WEIGHTS), "sha256": hashlib.sha256(WEIGHTS).hexdigest()}
    manifest.write_text(
        json.dumps(
            {
                "format": 1,
                "model": "example/model",
                "revision": None,
                "license": "Apache-2.0",
                "files": {"weights.safetensors": entry},
            }
        ),
        encoding="utf-8",
    )
    return directory, manifest


def test_ner_is_off_by_default() -> None:
    settings = _settings()
    assert settings.ner_enabled is False
    assert settings.ner_model_dir is None
    assert settings.ner_workers == 1
    assert settings.ner_timeout_seconds == 10.0
    assert settings.ner_threshold == 0.5
    assert settings.ner_cache_entries == 10_000
    assert ner_from_settings(settings) is None


def test_ner_settings_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "ANTIFAZ_NER_ENABLED": "true",
        "ANTIFAZ_NER_MODEL_DIR": "/models/gliner",
        "ANTIFAZ_NER_TIMEOUT_SECONDS": "3.5",
        "ANTIFAZ_NER_WORKERS": "2",
        "ANTIFAZ_NER_THRESHOLD": "0.7",
        "ANTIFAZ_NER_CACHE_ENTRIES": "0",
    }.items():
        monkeypatch.setenv(name, value)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.ner_enabled is True
    assert settings.ner_model_dir == Path("/models/gliner")
    assert (settings.ner_timeout_seconds, settings.ner_workers) == (3.5, 2)
    assert (settings.ner_threshold, settings.ner_cache_entries) == (0.7, 0)


@pytest.mark.parametrize(
    "values",
    [
        {"ner_timeout_seconds": 0},
        {"ner_workers": 0},
        {"ner_workers": 65},
        {"ner_threshold": 0},
        {"ner_threshold": 1.5},
        {"ner_cache_entries": -1},
    ],
)
def test_invalid_ner_settings_are_refused(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _settings(**values)


def test_enabled_without_a_model_directory_refuses_to_start() -> None:
    with pytest.raises(UnsafeConfigError, match="ANTIFAZ_NER_MODEL_DIR"):
        ner_from_settings(_settings(ner_enabled=True))


def test_enabled_with_the_placeholder_manifest_refuses_to_start(tmp_path: Path) -> None:
    # This version ships no model: turning the NER on can never start "without NER" silently.
    with pytest.raises(UnsafeConfigError, match="manifest"):
        create_app(_settings(ner_enabled=True, ner_model_dir=tmp_path))


def test_a_model_that_does_not_match_its_manifest_refuses_to_start(
    model: tuple[Path, Path],
) -> None:
    directory, manifest = model
    (directory / "weights.safetensors").write_bytes(WEIGHTS[:-1] + b"?")
    settings = _settings(ner_enabled=True, ner_model_dir=directory)
    with pytest.raises(UnsafeConfigError, match="SHA-256") as error:
        ner_from_settings(settings, manifest_path=manifest)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_a_missing_backend_refuses_to_start(model: tuple[Path, Path]) -> None:
    directory, manifest = model
    settings = _settings(ner_enabled=True, ner_model_dir=directory)
    assert GLINER_FACTORY.startswith("antifaz.detect.ner.")
    for factory in (GLINER_FACTORY, "no_such_package_xyz.backend:create"):
        with pytest.raises(UnsafeConfigError, match="antifaz\\[ner\\]"):
            ner_from_settings(settings, manifest_path=manifest, factory=factory)


def test_a_matching_model_and_backend_build_the_detector(model: tuple[Path, Path]) -> None:
    directory, manifest = model
    settings = _settings(
        ner_enabled=True, ner_model_dir=directory, ner_workers=1, ner_timeout_seconds=5
    )
    detector = ner_from_settings(
        settings,
        manifest_path=manifest,
        factory=FAKE_FACTORY,
        options={"names": {CARMEN: "person"}},
    )
    assert isinstance(detector, NerDetector)
    detector.start()
    try:
        (spans,) = detector.find_many([f"Soy {CARMEN}"])
        assert [(s.start, s.end) for s in spans] == [(4, 4 + len(CARMEN))]
    finally:
        detector.close()
