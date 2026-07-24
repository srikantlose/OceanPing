import dataclasses

import numpy as np
import pytest

from training import federated


class _FakeEmbedder:
    """Deterministic stand-in for sentence-transformers, same convention as
    test_train_classifier.py's fake: two easily separable clusters based on
    whether "flood" appears in the text."""

    def encode(self, texts, normalize_embeddings=True):
        return np.array([[1.0, 0.0] if "flood" in t.lower() else [0.0, 1.0] for t in texts])


def _examples(n=8):
    exs = []
    for i in range(n):
        exs.append({"text": f"flood flood {i}", "hazard_type": "coastal_flooding"})
        exs.append({"text": f"oil spill {i}", "hazard_type": "oil_spill"})
    return exs


# --- federated_average -------------------------------------------------------


def test_federated_average_computes_weighted_mean():
    coef, intercept = federated.federated_average(
        [np.array([1.0, 1.0]), np.array([4.0, 4.0])],
        [np.array(0.0), np.array(2.0)],
        [3, 1],
    )
    assert coef == pytest.approx([1.75, 1.75])
    assert intercept == pytest.approx(0.5)


def test_federated_average_raises_with_no_weight():
    with pytest.raises(ValueError):
        federated.federated_average([np.array([1.0])], [np.array(0.0)], [0])


def test_federated_classifier_only_carries_numeric_parameters_not_raw_data():
    # Structural guarantee, not just convention: the object that crosses back
    # from a federated round has no field a caller could even try to stash
    # raw text or per-example embeddings into.
    field_names = {f.name for f in dataclasses.fields(federated.FederatedClassifier)}
    assert field_names == {"classes", "coefs", "intercepts"}


# --- partition_into_shards ----------------------------------------------------


def test_partition_into_shards_covers_every_item_exactly_once():
    items = list(range(11))
    shards = federated.partition_into_shards(items, 3, seed=1)
    assert len(shards) == 3
    flattened = sorted(i for shard in shards for i in shard)
    assert flattened == items


def test_partition_into_shards_sizes_differ_by_at_most_one():
    shards = federated.partition_into_shards(list(range(10)), 3, seed=0)
    sizes = sorted(len(s) for s in shards)
    assert sizes[-1] - sizes[0] <= 1


# --- simulate_federated_round -------------------------------------------------


def test_simulate_federated_round_excludes_a_class_no_shard_can_fit():
    # Neither shard has both a positive and negative example for "b", so "b"
    # never gets federated this round — it's dropped, not fabricated.
    shard0 = (np.array([[1.0, 0.0], [1.0, 0.0]]), ["a", "a"])
    shard1 = (np.array([[1.0, 0.0], [1.0, 0.0]]), ["a", "a"])
    model = federated.simulate_federated_round([shard0, shard1], ["a", "b"], seed=0)
    assert set(model.coefs.keys()) == set()


def test_simulate_federated_round_still_federates_a_class_one_shard_alone_can_fit():
    shard0 = (np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]), ["a", "a", "b", "b"])
    shard1 = (np.array([[1.0, 0.0], [1.0, 0.0]]), ["a", "a"])  # can't fit "b" alone
    model = federated.simulate_federated_round([shard0, shard1], ["a", "b"], seed=0)
    assert set(model.coefs.keys()) == {"a", "b"}


def test_federated_classifier_predicts_separable_clusters():
    shard0 = (np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]), ["a", "a", "b", "b"])
    shard1 = (np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]), ["a", "a", "b", "b"])
    model = federated.simulate_federated_round([shard0, shard1], ["a", "b"], seed=0)
    preds = model.predict(np.array([[1.0, 0.0], [0.0, 1.0]]))
    assert preds == ["a", "b"]


def test_predict_raises_when_no_class_was_federated():
    model = federated.FederatedClassifier(classes=["a", "b"])
    with pytest.raises(ValueError):
        model.predict(np.array([[1.0, 0.0]]))


# --- run_spike -----------------------------------------------------------------


def test_run_spike_returns_none_with_fewer_than_two_usable_classes(monkeypatch):
    monkeypatch.setattr(federated.nlp_classifier, "_load_model", lambda: _FakeEmbedder())
    assert federated.run_spike([{"text": "flood", "hazard_type": "coastal_flooding"}]) is None


def test_run_spike_returns_none_when_embedder_unavailable(monkeypatch):
    monkeypatch.setattr(federated.nlp_classifier, "_load_model", lambda: None)
    assert federated.run_spike(_examples()) is None


def test_run_spike_reports_federated_and_centralized_metrics_side_by_side(monkeypatch):
    monkeypatch.setattr(federated.nlp_classifier, "_load_model", lambda: _FakeEmbedder())
    result = federated.run_spike(_examples(10), n_shards=3, seed=0)

    assert result is not None
    assert result.n_shards == 3
    assert 0 < sum(result.per_shard_counts) < result.n_examples  # some held out for eval
    assert "f1_macro" in result.federated_metrics
    assert "f1_macro" in result.centralized_metrics
    # Cleanly separable synthetic clusters: both approaches should do well,
    # not necessarily identically, since federated averages independent
    # per-shard fits rather than one global fit.
    assert result.federated_metrics["f1_macro"] >= 0.5
    assert result.centralized_metrics["f1_macro"] >= 0.5


def test_run_spike_reports_zero_f1_instead_of_crashing_when_no_shard_can_federate_anything(monkeypatch):
    # A corpus so small that even the minimum viable split (n_shards=2) leaves
    # no shard with >=2 examples of both sides of any class's binary problem —
    # exactly what a real, still-tiny training_examples table looks like.
    monkeypatch.setattr(federated.nlp_classifier, "_load_model", lambda: _FakeEmbedder())
    examples = [
        {"text": "flood a", "hazard_type": "coastal_flooding"},
        {"text": "flood b", "hazard_type": "coastal_flooding"},
        {"text": "oil a", "hazard_type": "oil_spill"},
        {"text": "oil b", "hazard_type": "oil_spill"},
    ]
    result = federated.run_spike(examples, n_shards=2, seed=0)

    assert result is not None
    assert result.federated_metrics == {"f1_macro": 0.0, "classes_covered": []}
    assert result.centralized_metrics["f1_macro"] >= 0.0  # centralized still computes fine


def test_run_spike_drops_classes_with_too_few_examples(monkeypatch):
    monkeypatch.setattr(federated.nlp_classifier, "_load_model", lambda: _FakeEmbedder())
    examples = _examples(10) + [{"text": "tsunami singleton", "hazard_type": "tsunami"}]
    result = federated.run_spike(examples, n_shards=3, seed=0)
    assert result is not None
    assert "tsunami" not in result.federated_metrics["classes_covered"]
