import numpy as np

from app.core.config import get_settings
from app.modules.nlp import classifier


def _reset_caches(monkeypatch):
    monkeypatch.setattr(classifier, "_model", None)
    monkeypatch.setattr(classifier, "_model_failed", False)
    monkeypatch.setattr(classifier, "_clf", None)
    monkeypatch.setattr(classifier, "_clf_failed", False)


class _FakeEmbedder:
    def encode(self, texts, normalize_embeddings=True):
        return np.array([[1.0, 0.0]])


class _FakeClf:
    classes_ = ["coastal_flooding", "oil_spill"]

    def predict_proba(self, vecs):
        return [[0.9, 0.1]]


def test_classify_uses_finetuned_artifact_when_available(monkeypatch):
    _reset_caches(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "nlp_mode", "finetuned")
    monkeypatch.setattr(classifier, "_load_model", lambda: _FakeEmbedder())
    monkeypatch.setattr(classifier, "_load_finetuned", lambda: _FakeClf())

    result = classifier.classify("kadal thanni vandhuruchu")
    assert result.mode == "finetuned"
    assert result.hazard_type == "coastal_flooding"
    assert result.scores["coastal_flooding"] == 0.9


def test_classify_degrades_to_keyword_when_no_finetuned_artifact(monkeypatch):
    _reset_caches(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "nlp_mode", "finetuned")
    monkeypatch.setattr(classifier, "_load_model", lambda: None)
    monkeypatch.setattr(classifier, "_load_finetuned", lambda: None)

    result = classifier.classify("black oil slick near the harbour")
    assert result.mode == "keyword"
    assert result.hazard_type == "oil_spill"


def test_classify_degrades_to_embedding_when_finetuned_artifact_unavailable(monkeypatch):
    _reset_caches(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "nlp_mode", "finetuned")
    monkeypatch.setattr(classifier, "_load_finetuned", lambda: None)

    from app.modules.nlp.prototypes import PROTOTYPES

    fake_model = _FakeEmbedder()
    # One prototype row per real hazard type, so classify()'s per-class mean
    # over PROTOTYPES doesn't hit an empty slice for hazards our fake doesn't cover.
    fake_labels = list(PROTOTYPES.keys())
    fake_matrix = np.tile([0.0, 1.0], (len(fake_labels), 1))

    def _fake_load_model():
        monkeypatch.setattr(classifier, "_model", fake_model)
        monkeypatch.setattr(classifier, "_prototype_matrix", fake_matrix)
        monkeypatch.setattr(classifier, "_prototype_labels", fake_labels)
        return fake_model

    monkeypatch.setattr(classifier, "_load_model", _fake_load_model)

    result = classifier.classify("some text")
    assert result.mode == "embedding"
    assert set(result.scores.keys()) == set(fake_labels)


def test_load_finetuned_skips_load_when_no_version_configured(monkeypatch):
    _reset_caches(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "nlp_model_version", "")

    assert classifier._load_finetuned() is None
    assert classifier._clf_failed is True


def test_load_finetuned_caches_failure_on_missing_artifact(monkeypatch, tmp_path):
    _reset_caches(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "nlp_model_version", "does-not-exist")
    monkeypatch.setattr(settings, "training_artifacts_dir", str(tmp_path))

    assert classifier._load_finetuned() is None
    assert classifier._clf_failed is True
    # Second call must not retry the (already-failed) load.
    assert classifier._load_finetuned() is None
