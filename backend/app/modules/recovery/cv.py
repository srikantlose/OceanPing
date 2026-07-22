"""Damage-assessment CV adapter (phase 3, milestone 7).

The plan names YOLOv8/SAM behind "the same lazy-load pattern as
nlp/classifier.py" — but a pretrained COCO detector doesn't actually know
what building damage looks like, and this environment has no labeled Indian
coastal-damage dataset to fine-tune one against (the plan's own risk section
says as much: "CV model quality... budget hand-labeling"). Shipping a generic
object detector and pretending its output is a damage classifier would be
theater, not capability — the same call this project already made for
CoastSnap and the satellite scene-scoring recipes.

So the real, verified signal here is classical: brightness, "water/mud" hue
fraction, and edge density computed directly from the photo's own pixels
(Pillow + numpy, no new dependency). It's coarse and explainable by design,
not a trained classifier — genuinely computed from what was actually
photographed, not a hash-based stand-in. `_load_yolo()` mirrors
nlp/classifier.py's lazy-load-with-cached-failure shape exactly: if
`ultralytics` is ever installed, real object detections are attached to
`detail` for analyst context (never used to override the heuristic's
class/severity, since generic COCO classes don't map to a damage taxonomy).
"""
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

log = logging.getLogger(__name__)

DAMAGE_CLASSES = ["flooding", "structural_or_debris", "minor_or_none"]
SEVERITIES = ["minor", "moderate", "severe", "destroyed"]

_yolo_lock = threading.Lock()
_yolo = None
_yolo_failed = False

# Water/mud fraction at or above this means the scene reads as dominated by
# standing water — checked before edge density since a flooded scene can be
# either glassy-calm (low edges) or debris-strewn (high edges) and either way
# "flooding" is the more useful label than "debris".
WATER_FLOODING_THRESHOLD = 0.35
WATER_SEVERE_THRESHOLD = 0.50
WATER_DESTROYED_THRESHOLD = 0.65

# Edge density above this reads as a visually chaotic scene (rubble, downed
# structures, scattered debris) rather than an intact one.
EDGE_DEBRIS_THRESHOLD = 0.18
EDGE_SEVERE_THRESHOLD = 0.28


@dataclass
class DamageResult:
    damage_class: str
    severity: str
    confidence: float
    mode: str  # "heuristic" | "heuristic+yolo"
    detail: dict = field(default_factory=dict)


def _load_yolo():
    """Lazy-load a real YOLOv8 detector if `ultralytics` is installed;
    load-once-cache-failure, identical shape to
    nlp/classifier.py::_load_model(). Not installed in this environment (see
    module docstring) — always falls through to the heuristic path here, the
    same honest "unit-tested seam, not exercised live" note milestone 4 gave
    the narrative LLM-polish path."""
    global _yolo, _yolo_failed
    if _yolo is not None or _yolo_failed:
        return _yolo
    with _yolo_lock:
        if _yolo is not None or _yolo_failed:
            return _yolo
        try:
            from ultralytics import YOLO

            _yolo = YOLO("yolov8n.pt")
            log.info("YOLOv8 detector loaded for damage-assessment context")
        except Exception:
            log.info("ultralytics unavailable; using pixel-heuristic damage triage only")
            _yolo_failed = True
    return _yolo


def detect_objects(path: Path) -> list[str] | None:
    """Generic object labels for analyst context only — never used to decide
    damage_class/severity (see module docstring). None if no detector loaded."""
    model = _load_yolo()
    if model is None:
        return None
    try:
        result = model(str(path), verbose=False)[0]
        return sorted({result.names[int(c)] for c in result.boxes.cls})
    except Exception:
        log.warning("YOLO inference failed for %s", path, exc_info=True)
        return None


def image_stats(path: Path) -> dict:
    """Real signals computed from the photo's own pixels: brightness, the
    fraction of pixels reading as blue-dominant (standing water), and edge
    density (visual chaos — rubble/debris vs. an intact scene)."""
    with Image.open(path) as img:
        rgb = img.convert("RGB").resize((256, 256))
        arr = np.asarray(rgb).astype(np.float64) / 255.0
        gray = np.asarray(rgb.convert("L").filter(ImageFilter.FIND_EDGES), dtype=np.float64) / 255.0
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    water_mask = (b > r) & (b >= g * 0.95)
    return {
        "brightness": round(float(arr.mean()), 4),
        "contrast": round(float(arr.std()), 4),
        "water_fraction": round(float(water_mask.mean()), 4),
        "edge_density": round(float(gray.mean()), 4),
    }


def classify_from_stats(stats: dict) -> tuple[str, str, float]:
    """Pure decision over precomputed stats — the part that's actually
    unit-testable without touching PIL/filesystem."""
    water = stats["water_fraction"]
    edges = stats["edge_density"]
    if water >= WATER_FLOODING_THRESHOLD:
        if water >= WATER_DESTROYED_THRESHOLD:
            severity = "destroyed"
        elif water >= WATER_SEVERE_THRESHOLD:
            severity = "severe"
        else:
            severity = "moderate"
        confidence = min(0.55, 0.25 + water * 0.4)
        return "flooding", severity, round(confidence, 3)
    if edges >= EDGE_DEBRIS_THRESHOLD:
        severity = "severe" if edges >= EDGE_SEVERE_THRESHOLD else "moderate"
        confidence = min(0.5, 0.2 + edges)
        return "structural_or_debris", severity, round(confidence, 3)
    return "minor_or_none", "minor", 0.3


def classify_damage(path: Path) -> DamageResult:
    stats = image_stats(path)
    damage_class, severity, confidence = classify_from_stats(stats)
    objects = detect_objects(path)
    detail = dict(stats)
    mode = "heuristic"
    if objects is not None:
        detail["detected_objects"] = objects
        mode = "heuristic+yolo"
    return DamageResult(
        damage_class=damage_class, severity=severity, confidence=confidence, mode=mode, detail=detail
    )
