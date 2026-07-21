"""Short-horizon forecasting — pure functions, no I/O.

Two independent models, both deliberately simple and explainable (the same
"cheap, defensible, real-data-only" judgment the bathtub inundation model
already made in place of ANUGA):

- Sensor forecasting: harmonic-trend least-squares regression (a linear trend
  plus semidiurnal/diurnal tidal harmonics) instead of Prophet. Prophet's
  Stan-compilation backend is heavy for a pilot deployment, and its
  daily/weekly seasonal components don't match the ~12.4h period this data
  actually has — real short-horizon tide/wave nowcasting overwhelmingly uses
  harmonic constituent analysis anyway, so this is the more defensible
  choice here, not just the lighter one.
- Hazard-front propagation: fit a constant-velocity front to a time-ordered
  report cluster (least-squares linear fit in a local km-projected plane),
  then translate current cells forward by elapsed time. A real front isn't
  linear or constant-speed, but over a 1-3h horizon this is a reasonable
  first approximation — same "simplest defensible model, upgrade path noted"
  call the bathtub model makes for flow routing.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import h3
import numpy as np

from app.modules.geo.h3utils import H3_RESOLUTION

MIN_SENSOR_POINTS = 20
# M2 semidiurnal (12.42h) + K1 diurnal (23.93h) tidal constituents — the two
# dominant real tidal harmonics, and a reasonable proxy for daily patterns in
# non-tidal variables (wave height, temperature) too.
SENSOR_HARMONIC_PERIODS_HOURS = (12.42, 23.93)

MIN_FRONT_POINTS = 4
MIN_FRONT_SPEED_KMH = 0.1  # below this, fitted "motion" is noise, not a front worth projecting


def _project_km(lats: np.ndarray, lons: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    lat0 = float(np.mean(lats))
    x = lons * 111.320 * math.cos(math.radians(lat0))
    y = lats * 110.574
    return x, y, lat0


def _unproject_km(dx_km: float, dy_km: float, lat0: float) -> tuple[float, float]:
    dlon = dx_km / (111.320 * math.cos(math.radians(lat0)))
    dlat = dy_km / 110.574
    return dlat, dlon


def _design_matrix(hours: np.ndarray) -> np.ndarray:
    cols = [np.ones_like(hours), hours]
    for period in SENSOR_HARMONIC_PERIODS_HOURS:
        omega = 2 * math.pi / period
        cols.append(np.sin(omega * hours))
        cols.append(np.cos(omega * hours))
    return np.column_stack(cols)


def fit_sensor_forecast(
    times: list[datetime], values: list[float], horizon_hours: float, step_minutes: int
) -> list[dict] | None:
    """Harmonic-trend regression forecast from a station/variable's history.
    None when there's too little history to fit — same data-gated-degrade
    pattern as anomaly detection's baseline floor."""
    if len(times) < MIN_SENSOR_POINTS:
        return None
    t0 = times[0]
    hours = np.array([(t - t0).total_seconds() / 3600 for t in times])
    y = np.array(values, dtype=float)
    coeffs, *_ = np.linalg.lstsq(_design_matrix(hours), y, rcond=None)

    last_hour = float(hours[-1])
    n_steps = max(1, round(horizon_hours * 60 / step_minutes))
    future_hours = last_hour + (np.arange(1, n_steps + 1) * (step_minutes / 60))
    preds = _design_matrix(future_hours) @ coeffs

    return [
        {"time": (t0 + timedelta(hours=float(h))).isoformat(), "value": round(float(v), 4)}
        for h, v in zip(future_hours, preds)
    ]


@dataclass
class Front:
    vx_kmh: float  # eastward component
    vy_kmh: float  # northward component
    speed_kmh: float
    bearing_deg: float  # compass bearing, 0 = north
    lat0: float  # projection reference latitude


def fit_front(sequence: list[tuple[datetime, float, float]]) -> Front | None:
    """Fit a constant-velocity hazard front to a time-ordered (time, lat, lon)
    report sequence. None if there's too little history, no elapsed time to
    fit against, or the fitted motion is too small to be worth projecting
    (jitter around one spot, not a moving front)."""
    if len(sequence) < MIN_FRONT_POINTS:
        return None
    sequence = sorted(sequence, key=lambda p: p[0])
    t0 = sequence[0][0]
    hours = np.array([(t - t0).total_seconds() / 3600 for t, _, _ in sequence])
    if hours[-1] - hours[0] < 1e-6:
        return None
    lats = np.array([lat for _, lat, _ in sequence])
    lons = np.array([lon for _, _, lon in sequence])
    x_km, y_km, lat0 = _project_km(lats, lons)

    A = np.column_stack([hours, np.ones_like(hours)])
    vx = float(np.linalg.lstsq(A, x_km, rcond=None)[0][0])
    vy = float(np.linalg.lstsq(A, y_km, rcond=None)[0][0])
    speed = math.hypot(vx, vy)
    if speed < MIN_FRONT_SPEED_KMH:
        return None
    bearing = math.degrees(math.atan2(vx, vy)) % 360
    return Front(vx_kmh=vx, vy_kmh=vy, speed_kmh=speed, bearing_deg=bearing, lat0=lat0)


def project_front_cells(
    front: Front, current_cells: set[str], hours_ahead: float, resolution: int = H3_RESOLUTION
) -> set[str]:
    """Translate every current cell's centroid by the front's velocity over
    `hours_ahead` and return the resulting cell set. The front doesn't
    retreat, so cells already under the hazard stay included — callers that
    only want the *new*, ahead-of-reports cells should subtract
    current_cells from the result."""
    if not current_cells:
        return set()
    dlat, dlon = _unproject_km(front.vx_kmh * hours_ahead, front.vy_kmh * hours_ahead, front.lat0)
    projected = set()
    for cell in current_cells:
        lat, lon = h3.cell_to_latlng(cell)
        projected.add(h3.latlng_to_cell(lat + dlat, lon + dlon, resolution))
    return projected
