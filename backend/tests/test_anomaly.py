import numpy as np

from app.modules.sensors.anomaly import MIN_BASELINE_POINTS, detect


def test_spike_detected():
    baseline = list(np.random.default_rng(7).normal(1.0, 0.1, 200))
    result = detect(baseline, 2.5)
    assert result is not None
    assert result.zscore > 10


def test_normal_reading_low_z():
    baseline = list(np.random.default_rng(7).normal(1.0, 0.1, 200))
    result = detect(baseline, 1.05)
    assert result is not None
    assert abs(result.zscore) < 1.5


def test_short_baseline_rejected():
    assert detect([1.0] * (MIN_BASELINE_POINTS - 1), 5.0) is None


def test_flat_baseline_rejected():
    assert detect([1.0] * 100, 5.0) is None
