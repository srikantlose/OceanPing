"""Trains the classify() 'finetuned' mode artifact: a logistic-regression head
over the same multilingual sentence embeddings classify() already uses in
embedding mode. This is a linear probe, not a full MuRIL/IndicBERT fine-tune —
scope matches the labeled-volume risk called out in the phase-1 plan (bootstrap
a corpus first; swap in a real transformer fine-tune once training_examples
clears the ~500-row threshold noted there). retrain.py is the operational
wrapper around this module: it exports labels, calls train(), and registers
the result in model_versions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from app.modules.nlp import classifier as nlp_classifier

log = logging.getLogger(__name__)

MIN_EXAMPLES_PER_CLASS = 2


@dataclass
class TrainResult:
    model: object
    metrics: dict
    n_train: int
    n_eval: int


def train(examples: list[dict], eval_fraction: float = 0.2, seed: int = 0) -> TrainResult | None:
    """examples: [{"text": str, "hazard_type": str}, ...], already filtered to
    verified (outcome == "verify") rows with non-empty text. Returns None if
    there isn't enough labeled data to train a meaningful model yet."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import f1_score
    from sklearn.model_selection import train_test_split

    texts = [e["text"] for e in examples]
    labels = [e["hazard_type"] for e in examples]

    counts: dict[str, int] = {}
    for lbl in labels:
        counts[lbl] = counts.get(lbl, 0) + 1
    usable_classes = {lbl for lbl, n in counts.items() if n >= MIN_EXAMPLES_PER_CLASS}
    if len(usable_classes) < 2:
        log.warning("Not enough labeled classes to train (%d usable, need >=2)", len(usable_classes))
        return None

    texts, labels = zip(*[(t, l) for t, l in zip(texts, labels) if l in usable_classes])

    embedder = nlp_classifier._load_model()
    if embedder is None:
        log.warning("Embedding model unavailable; cannot train a finetuned classifier")
        return None
    embeddings = np.array(embedder.encode(list(texts), normalize_embeddings=True))

    try:
        x_train, x_eval, y_train, y_eval = train_test_split(
            embeddings, list(labels), test_size=eval_fraction, random_state=seed
        )
        if len(set(y_train)) < 2:
            raise ValueError("train split collapsed to one class")
        held_out = True
    except ValueError:
        # Too little data per class to hold out a meaningful split — fit and
        # report training-set fit instead, clearly flagged as such.
        x_train, y_train = embeddings, list(labels)
        x_eval, y_eval = embeddings, list(labels)
        held_out = False

    clf = LogisticRegression(max_iter=1000)
    clf.fit(x_train, y_train)
    preds = clf.predict(x_eval)
    metrics = {
        "f1_macro": round(float(f1_score(y_eval, preds, average="macro")), 4),
        "n_classes": len(usable_classes),
        "classes": sorted(usable_classes),
        "held_out_eval": held_out,
    }
    return TrainResult(model=clf, metrics=metrics, n_train=len(x_train), n_eval=len(x_eval))
