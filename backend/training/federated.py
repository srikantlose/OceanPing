"""Federated-averaging spike for the hazard classifier (phase 4, milestone 6:
"federated learning spike -> go/no-go", docs/plans/phase-4-scale.md). This is
a working prototype sized to answer that question with real numbers from this
app's own verified-report corpus, not a production federation service.

Why the linear-probe text classifier and not the image damage classifier: the
plan's own text names "the image damage classifier (clearest label source)"
as the starting point, but modules/recovery/cv.py has no trainable parameters
at all — classify_from_stats() is a fixed threshold table over Pillow pixel
statistics, not a fitted model (see that module's own docstring). There is
nothing to federate there yet. training/train_classifier.py's
LogisticRegression head over sentence-transformer embeddings is the only
subsystem in this app with real, versioned, retrained weights
(model_versions/training_examples, phase 1 milestone 4), so this spike
targets that classifier instead — see docs/plans/phase-4-scale.md's milestone
6 write-up for why that substitution matters for the go/no-go call.

DPDP data-flow story: FederatedClassifier only ever carries three fields —
classes, and per-class coefficient/intercept arrays (see its own dataclass
below) — never text, never labeled embeddings. federated_average() is the one
function that combines information across simulated devices, and its
signature only accepts numeric arrays and integer weights, so a caller cannot
pass raw text or per-example data through it even by mistake. This simulation
computes embeddings centrally only because it is standing in for N separate
phones in one process; a real deployment would ship the same frozen
sentence-transformer to the device (no training happens on it, exactly as
today) and run local_update-equivalent code there, so text and embeddings
alike would never leave the phone — only the coefficient arrays this
simulation already treats as the sole cross-device payload would cross the
network.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from app.modules.nlp import classifier as nlp_classifier

log = logging.getLogger(__name__)

MIN_PER_CLASS = 2  # mirrors train_classifier.MIN_EXAMPLES_PER_CLASS


def federated_average(
    coefs: list[np.ndarray], intercepts: list[np.ndarray], weights: list[int]
) -> tuple[np.ndarray, np.ndarray]:
    """Weighted mean of per-shard model parameters — the FedAvg aggregation
    step (McMahan et al., 2017). weights are each shard's local example
    count, so a device with more local examples pulls the average further
    toward its own update, same as the original FedAvg paper. Only numeric
    parameter arrays are accepted here — no shard's raw text or embeddings
    ever reach this function, which is the DPDP-relevant property this spike
    exists to demonstrate."""
    if not coefs or sum(weights) == 0:
        raise ValueError("federated_average needs at least one shard with a positive weight")
    coef = np.average(np.stack(coefs), axis=0, weights=weights)
    intercept = np.average(np.stack(intercepts), axis=0, weights=weights)
    return coef, intercept


def partition_into_shards(items: list, n_shards: int, seed: int = 0) -> list[list]:
    """Randomly splits items round-robin across n_shards simulated devices.
    Every item lands in exactly one shard; shard sizes differ by at most one."""
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(items))
    shards: list[list] = [[] for _ in range(n_shards)]
    for i, idx in enumerate(order):
        shards[i % n_shards].append(items[int(idx)])
    return shards


def _fit_binary(embeddings: np.ndarray, y_binary: np.ndarray, seed: int) -> tuple[np.ndarray, float] | None:
    from sklearn.linear_model import LogisticRegression

    if len(set(y_binary.tolist())) < 2:
        return None
    clf = LogisticRegression(max_iter=1000, random_state=seed)
    clf.fit(embeddings, y_binary)
    return clf.coef_[0], float(clf.intercept_[0])


@dataclass
class FederatedClassifier:
    """A hand-rolled one-vs-rest ensemble: one binary linear model per class,
    each independently federated-averaged across shards. Chosen over a single
    shared multinomial LogisticRegression specifically because a binary "is
    this class or not" problem is always poolable across non-IID shards (a
    fixed coefficient shape regardless of which other classes a shard happens
    to contain) — a shared multinomial coef_ matrix is not: its shape and row
    order depend on which classes appear in that shard's local data, which
    real devices can't be relied on to match.

    These three fields are the entire object — no text, no per-example
    embeddings, nothing that ever touched a specific report."""

    classes: list[str]
    coefs: dict = field(default_factory=dict)  # class -> np.ndarray
    intercepts: dict = field(default_factory=dict)  # class -> float

    def decision_scores(self, embeddings: np.ndarray) -> dict[str, np.ndarray]:
        return {
            cls: embeddings @ self.coefs[cls] + self.intercepts[cls]
            for cls in self.classes
            if cls in self.coefs
        }

    def predict(self, embeddings: np.ndarray) -> list[str]:
        scores = self.decision_scores(embeddings)
        available = list(scores.keys())
        if not available:
            raise ValueError("no class was federated successfully — nothing to predict with")
        stacked = np.stack([scores[c] for c in available], axis=1)
        best = np.argmax(stacked, axis=1)
        return [available[i] for i in best]


def simulate_federated_round(
    shards: list[tuple[np.ndarray, list[str]]], classes: list[str], seed: int = 0
) -> FederatedClassifier:
    """One round of FedAvg over a one-vs-rest linear model: for each class,
    every shard with at least MIN_PER_CLASS positive and MIN_PER_CLASS
    negative examples for that class fits its own local binary classifier;
    the server then federated_average()s just their coefficient arrays,
    weighted by each shard's local example count. A shard that can't fit a
    class (too few examples either way) is simply excluded from that class's
    average, not treated as an error — real devices will routinely have zero
    examples of most classes, which is exactly why per-class weighting
    matters instead of assuming every shard sees every class."""
    model = FederatedClassifier(classes=list(classes))
    for cls in classes:
        coefs, intercepts, weights = [], [], []
        for embeddings, labels in shards:
            y_binary = np.array([1 if lbl == cls else 0 for lbl in labels])
            if y_binary.sum() < MIN_PER_CLASS or (len(y_binary) - y_binary.sum()) < MIN_PER_CLASS:
                continue
            fit = _fit_binary(embeddings, y_binary, seed)
            if fit is None:
                continue
            coef, intercept = fit
            coefs.append(coef)
            intercepts.append(np.array(intercept))
            weights.append(len(labels))
        if not coefs:
            log.warning("No shard had enough examples to federate class %r this round", cls)
            continue
        coef, intercept = federated_average(coefs, intercepts, weights)
        model.coefs[cls] = coef
        model.intercepts[cls] = float(intercept)
    return model


@dataclass
class SpikeResult:
    federated_metrics: dict
    centralized_metrics: dict
    n_examples: int
    n_shards: int
    per_shard_counts: list[int]


def run_spike(
    examples: list[dict], n_shards: int = 5, eval_fraction: float = 0.2, seed: int = 0
) -> SpikeResult | None:
    """The end-to-end comparison: the same labeled corpus train_classifier.
    train() already retrains the centralized model on, held out the same way,
    but the training portion is partitioned across n_shards simulated devices
    and combined via simulate_federated_round() instead of one server-side
    fit. Both paths train and evaluate on the identical split, so the only
    difference measured is "federated vs. centralized," not "different data."
    Returns None under the same "not enough labeled data" condition
    train_classifier.train() already uses."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import f1_score
    from sklearn.model_selection import train_test_split

    from training.train_classifier import MIN_EXAMPLES_PER_CLASS

    texts = [e["text"] for e in examples]
    labels = [e["hazard_type"] for e in examples]
    counts: dict[str, int] = {}
    for lbl in labels:
        counts[lbl] = counts.get(lbl, 0) + 1
    usable_classes = sorted(lbl for lbl, n in counts.items() if n >= MIN_EXAMPLES_PER_CLASS)
    if len(usable_classes) < 2:
        log.warning("Not enough labeled classes to run the spike (%d usable, need >=2)", len(usable_classes))
        return None

    texts, labels = zip(*[(t, l) for t, l in zip(texts, labels) if l in usable_classes])
    texts, labels = list(texts), list(labels)

    embedder = nlp_classifier._load_model()
    if embedder is None:
        log.warning("Embedding model unavailable; cannot run the spike")
        return None
    embeddings = np.array(embedder.encode(texts, normalize_embeddings=True))

    try:
        x_train, x_eval, y_train, y_eval = train_test_split(
            embeddings, labels, test_size=eval_fraction, random_state=seed
        )
        if len(set(y_train)) < 2:
            raise ValueError("train split collapsed to one class")
    except ValueError:
        # Too little data per class to hold out a meaningful split — fit and
        # evaluate on the same set instead, same fallback as train_classifier.train().
        x_train, y_train = embeddings, labels
        x_eval, y_eval = embeddings, labels

    shard_indices = partition_into_shards(list(range(len(y_train))), n_shards, seed=seed)
    shards = [(x_train[idx], [y_train[i] for i in idx]) for idx in shard_indices]

    federated_model = simulate_federated_round(shards, usable_classes, seed=seed)
    if not federated_model.coefs:
        # No shard had enough local examples of any class to fit even one
        # binary model — a real outcome at small corpus sizes, not a crash.
        # f1=0.0 reports it plainly rather than raising out of a comparison
        # whose whole point is to surface exactly this kind of failure.
        federated_metrics = {"f1_macro": 0.0, "classes_covered": []}
    else:
        fed_preds = federated_model.predict(x_eval)
        federated_metrics = {
            "f1_macro": round(float(f1_score(y_eval, fed_preds, average="macro")), 4),
            "classes_covered": sorted(federated_model.coefs.keys()),
        }

    centralized_clf = LogisticRegression(max_iter=1000, random_state=seed)
    centralized_clf.fit(x_train, y_train)
    central_preds = centralized_clf.predict(x_eval)
    centralized_metrics = {
        "f1_macro": round(float(f1_score(y_eval, central_preds, average="macro")), 4),
    }

    return SpikeResult(
        federated_metrics=federated_metrics,
        centralized_metrics=centralized_metrics,
        n_examples=len(labels),
        n_shards=n_shards,
        per_shard_counts=[len(idx) for idx in shard_indices],
    )
