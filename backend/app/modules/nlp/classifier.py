"""Hazard classification: multilingual embeddings vs. per-class prototypes,
with a keyword fallback when the model is unavailable (NLP_MODE=keyword).

Callers use classify() / embed() only, so a fine-tuned model can replace the
internals without touching the ingest pipeline. NLP_MODE=finetuned tries a
retrain.py artifact (a logistic-regression head over the same embeddings, see
backend/training/train_classifier.py) before falling back to embedding/keyword,
exactly like the embedding model's own load-failure fallback below.
"""
import logging
import os
import threading
from dataclasses import dataclass, field

import numpy as np

from app.core.config import get_settings
from app.modules.nlp.prototypes import KEYWORDS, PROTOTYPES, URGENCY_HIGH, URGENCY_LOW

log = logging.getLogger(__name__)

_lock = threading.Lock()
_model = None
_prototype_matrix: np.ndarray | None = None
_prototype_labels: list[str] = []
_model_failed = False

_clf_lock = threading.Lock()
_clf = None
_clf_failed = False


@dataclass
class Classification:
    hazard_type: str | None
    scores: dict = field(default_factory=dict)
    embedding: list[float] | None = None
    mode: str = "keyword"


def detect_lang(text: str) -> str:
    try:
        from langdetect import detect

        return detect(text)
    except Exception:
        return "und"


def detect_urgency(text: str | None, default: str = "medium") -> str:
    if not text:
        return default
    lower = text.lower()
    if any(k in lower for k in URGENCY_HIGH):
        return "high"
    if any(k in lower for k in URGENCY_LOW):
        return "low"
    return default


def _load_model():
    """Lazy-load sentence-transformers; on failure fall back to keyword mode."""
    global _model, _prototype_matrix, _prototype_labels, _model_failed
    if _model is not None or _model_failed:
        return _model
    with _lock:
        if _model is not None or _model_failed:
            return _model
        try:
            from sentence_transformers import SentenceTransformer

            settings = get_settings()
            model = SentenceTransformer(settings.embedding_model)
            labels, phrases = [], []
            for hazard, plist in PROTOTYPES.items():
                for p in plist:
                    labels.append(hazard)
                    phrases.append(p)
            matrix = model.encode(phrases, normalize_embeddings=True)
            _model, _prototype_matrix, _prototype_labels = model, matrix, labels
            log.info("Embedding model loaded: %s", settings.embedding_model)
        except Exception:
            log.exception("Embedding model unavailable; using keyword fallback")
            _model_failed = True
    return _model


def _load_finetuned():
    """Lazy-load the retrain.py artifact named by settings.nlp_model_version.
    Load-once-cache-failure, same shape as _load_model() above: a missing
    version, missing file, or corrupt artifact just falls back, it never
    raises out of classify()."""
    global _clf, _clf_failed
    if _clf is not None or _clf_failed:
        return _clf
    with _clf_lock:
        if _clf is not None or _clf_failed:
            return _clf
        settings = get_settings()
        if not settings.nlp_model_version:
            _clf_failed = True
            return None
        path = os.path.join(settings.training_artifacts_dir, settings.nlp_model_version, "classifier.joblib")
        try:
            import joblib

            _clf = joblib.load(path)
            log.info("Fine-tuned classifier loaded: %s", settings.nlp_model_version)
        except Exception:
            log.exception("Fine-tuned classifier unavailable (%s); falling back", path)
            _clf_failed = True
    return _clf


def embed(text: str) -> list[float] | None:
    if get_settings().nlp_mode != "embedding":
        return None
    model = _load_model()
    if model is None:
        return None
    vec = model.encode([text], normalize_embeddings=True)[0]
    return vec.tolist()


def _classify_keywords(text: str) -> Classification:
    lower = text.lower()
    scores = {
        hazard: sum(1 for k in kws if k.lower() in lower)
        for hazard, kws in KEYWORDS.items()
    }
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return Classification(hazard_type=None, scores=scores, mode="keyword")
    return Classification(hazard_type=best, scores=scores, mode="keyword")


def classify(text: str | None) -> Classification:
    """Return the best hazard class for free text, or None if unclear."""
    if not text or not text.strip():
        return Classification(hazard_type=None)
    settings = get_settings()
    if settings.nlp_mode == "finetuned":
        embedder = _load_model()
        clf = _load_finetuned()
        if embedder is not None and clf is not None:
            vec = np.array(embedder.encode([text], normalize_embeddings=True)[0])
            proba = clf.predict_proba([vec])[0]
            per_class = {str(lbl): round(float(p), 4) for lbl, p in zip(clf.classes_, proba)}
            best = max(per_class, key=per_class.get)
            hazard = best if per_class[best] >= settings.classify_threshold else None
            return Classification(hazard_type=hazard, scores=per_class, embedding=vec.tolist(), mode="finetuned")
        # No trained artifact (or embedder) available yet — degrade to embedding/keyword.
    if settings.nlp_mode in ("embedding", "finetuned") and _load_model() is not None:
        vec = np.array(_model.encode([text], normalize_embeddings=True)[0])
        sims = _prototype_matrix @ vec
        # Mean of top-2 prototype similarities per class.
        per_class: dict[str, float] = {}
        for hazard in PROTOTYPES:
            class_sims = sorted(
                (s for s, lbl in zip(sims, _prototype_labels) if lbl == hazard),
                reverse=True,
            )[:2]
            per_class[hazard] = float(np.mean(class_sims))
        best = max(per_class, key=per_class.get)
        hazard = best if per_class[best] >= settings.classify_threshold else None
        return Classification(
            hazard_type=hazard,
            scores={k: round(v, 4) for k, v in per_class.items()},
            embedding=vec.tolist(),
            mode="embedding",
        )
    return _classify_keywords(text)
