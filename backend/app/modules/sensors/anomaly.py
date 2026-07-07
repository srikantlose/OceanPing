"""Anomaly detection v1: rolling z-score of the latest reading against a
trailing baseline window. Deliberately simple; upgradeable to seasonal models."""
from dataclasses import dataclass

import numpy as np

MIN_BASELINE_POINTS = 30
MIN_STD = 1e-9


@dataclass
class AnomalyResult:
    zscore: float
    value: float
    baseline_mean: float
    baseline_std: float


def detect(baseline: list[float], latest: float) -> AnomalyResult | None:
    """z-score of `latest` vs. `baseline`; None when the baseline is unusable."""
    if len(baseline) < MIN_BASELINE_POINTS:
        return None
    arr = np.asarray(baseline, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std())
    if std < MIN_STD:
        return None
    z = (latest - mean) / std
    return AnomalyResult(zscore=float(z), value=latest, baseline_mean=mean, baseline_std=std)
