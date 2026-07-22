import numpy as np
import pytest
from PIL import Image

from app.modules.recovery import cv


def _save(tmp_path, name, rgb_array):
    path = tmp_path / name
    Image.fromarray(rgb_array.astype("uint8")).save(path)
    return path


def _solid(color: tuple[int, int, int]) -> np.ndarray:
    arr = np.zeros((64, 64, 3), dtype="uint8")
    arr[:, :, 0], arr[:, :, 1], arr[:, :, 2] = color
    return arr


def _checkerboard() -> np.ndarray:
    """Equal channels (never reads as "water") but maximal pixel-to-pixel
    contrast — a deterministic stand-in for a visually chaotic debris scene,
    without depending on RNG state for reproducibility."""
    xv, yv = np.meshgrid(np.arange(64), np.arange(64))
    plane = ((xv + yv) % 2 * 255).astype("uint8")
    return np.stack([plane, plane, plane], axis=-1)


# --- image_stats -------------------------------------------------------------


def test_image_stats_blue_image_reads_as_mostly_water(tmp_path):
    path = _save(tmp_path, "blue.png", _solid((20, 40, 200)))
    stats = cv.image_stats(path)
    assert stats["water_fraction"] > 0.9
    # A solid-color source can still pick up a whisper of edge signal from
    # PNG/resize interpolation noise — nowhere near the debris threshold.
    assert stats["edge_density"] < 0.02


def test_image_stats_gray_image_has_no_water_and_no_edges(tmp_path):
    path = _save(tmp_path, "gray.png", _solid((128, 128, 128)))
    stats = cv.image_stats(path)
    assert stats["water_fraction"] == 0.0
    assert stats["edge_density"] < 0.02


def test_image_stats_checkerboard_has_high_edge_density_no_water(tmp_path):
    path = _save(tmp_path, "checker.png", _checkerboard())
    stats = cv.image_stats(path)
    assert stats["water_fraction"] == 0.0
    assert stats["edge_density"] > cv.EDGE_DEBRIS_THRESHOLD


# --- classify_from_stats (pure) ----------------------------------------------


def test_classify_flooding_scales_severity_with_water_fraction():
    cls, severity, conf = cv.classify_from_stats({"water_fraction": 0.9, "edge_density": 0.0})
    assert cls == "flooding"
    assert severity == "destroyed"
    assert 0 < conf <= 0.55


def test_classify_moderate_flooding_just_above_threshold():
    cls, severity, _ = cv.classify_from_stats({"water_fraction": 0.4, "edge_density": 0.0})
    assert cls == "flooding"
    assert severity == "moderate"


def test_classify_debris_from_high_edge_density():
    cls, severity, _ = cv.classify_from_stats({"water_fraction": 0.0, "edge_density": 0.3})
    assert cls == "structural_or_debris"
    assert severity == "severe"


def test_classify_minor_when_scene_is_calm_and_dry():
    cls, severity, conf = cv.classify_from_stats({"water_fraction": 0.0, "edge_density": 0.0})
    assert cls == "minor_or_none"
    assert severity == "minor"
    assert conf == 0.3


def test_water_signal_takes_priority_over_edge_signal():
    """A flooded *and* debris-strewn scene should read as flooding — the
    more actionable label for a responder deciding where boats/pumps go."""
    cls, _, _ = cv.classify_from_stats({"water_fraction": 0.5, "edge_density": 0.5})
    assert cls == "flooding"


# --- classify_damage (full pipeline) ------------------------------------------


def test_classify_damage_falls_back_to_heuristic_when_no_detector(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "_load_yolo", lambda: None)
    path = _save(tmp_path, "blue.png", _solid((20, 40, 200)))

    result = cv.classify_damage(path)

    assert result.mode == "heuristic"
    assert result.damage_class == "flooding"
    assert "detected_objects" not in result.detail


def test_classify_damage_attaches_yolo_context_without_changing_the_class(tmp_path, monkeypatch):
    """Object detections are analyst context only — see cv.py's module
    docstring on why a generic COCO detector never overrides the heuristic's
    damage_class/severity."""
    monkeypatch.setattr(cv, "detect_objects", lambda path: ["car", "boat"])
    path = _save(tmp_path, "gray.png", _solid((128, 128, 128)))

    result = cv.classify_damage(path)

    assert result.mode == "heuristic+yolo"
    assert result.damage_class == "minor_or_none"  # unchanged by the detections
    assert result.detail["detected_objects"] == ["car", "boat"]


def test_load_yolo_caches_failure(monkeypatch):
    """Same load-once-cache-failure shape as nlp/classifier.py::_load_model —
    an import failure should short-circuit on the next call rather than
    retrying the import every time."""
    monkeypatch.setattr(cv, "_yolo", None)
    monkeypatch.setattr(cv, "_yolo_failed", False)
    calls = {"n": 0}

    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "ultralytics":
            calls["n"] += 1
            raise ImportError("no ultralytics here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert cv._load_yolo() is None
    assert cv._load_yolo() is None
    assert calls["n"] == 1
