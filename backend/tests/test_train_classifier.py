import numpy as np

from training import train_classifier


class _FakeEmbedder:
    """Deterministic stand-in for sentence-transformers: two easily separable
    clusters based on whether "flood" appears in the text."""

    def encode(self, texts, normalize_embeddings=True):
        return np.array([[1.0, 0.0] if "flood" in t.lower() else [0.0, 1.0] for t in texts])


def _examples():
    return [
        {"text": "flood flood flood a", "hazard_type": "coastal_flooding"},
        {"text": "flood flood b", "hazard_type": "coastal_flooding"},
        {"text": "flood c", "hazard_type": "coastal_flooding"},
        {"text": "oil spill a", "hazard_type": "oil_spill"},
        {"text": "oil spill b", "hazard_type": "oil_spill"},
        {"text": "oil spill c", "hazard_type": "oil_spill"},
    ]


def test_train_returns_none_with_fewer_than_two_usable_classes(monkeypatch):
    monkeypatch.setattr(train_classifier.nlp_classifier, "_load_model", lambda: _FakeEmbedder())
    assert train_classifier.train([{"text": "flood", "hazard_type": "coastal_flooding"}]) is None


def test_train_returns_none_when_embedder_unavailable(monkeypatch):
    monkeypatch.setattr(train_classifier.nlp_classifier, "_load_model", lambda: None)
    assert train_classifier.train(_examples()) is None


def test_train_fits_a_separable_classifier(monkeypatch):
    monkeypatch.setattr(train_classifier.nlp_classifier, "_load_model", lambda: _FakeEmbedder())
    result = train_classifier.train(_examples())
    assert result is not None
    assert result.metrics["n_classes"] == 2
    assert result.metrics["f1_macro"] == 1.0
    assert result.model.predict([[1.0, 0.0]])[0] == "coastal_flooding"
    assert result.model.predict([[0.0, 1.0]])[0] == "oil_spill"


def test_train_drops_classes_with_too_few_examples(monkeypatch):
    monkeypatch.setattr(train_classifier.nlp_classifier, "_load_model", lambda: _FakeEmbedder())
    examples = _examples() + [{"text": "tsunami singleton", "hazard_type": "tsunami"}]
    result = train_classifier.train(examples)
    assert result is not None
    assert "tsunami" not in result.metrics["classes"]
