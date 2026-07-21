import math
from datetime import datetime, timedelta, timezone

import h3
import pytest

from app.modules.forecast import engine


def _hourly_times(n, start=None):
    start = start or datetime(2026, 7, 1, tzinfo=timezone.utc)
    return [start + timedelta(hours=h) for h in range(n)]


# --- fit_sensor_forecast ----------------------------------------------------

def test_fit_sensor_forecast_returns_none_with_too_few_points():
    times = _hourly_times(5)
    values = [1.0] * 5
    assert engine.fit_sensor_forecast(times, values, horizon_hours=2, step_minutes=30) is None


def test_fit_sensor_forecast_recovers_a_known_harmonic_plus_trend_signal():
    times = _hourly_times(24 * 10)  # 10 days hourly

    def true_value(h):
        return 1.0 + 0.01 * h + 0.3 * math.sin(2 * math.pi * h / 12.42)

    values = [true_value(h) for h in range(len(times))]
    points = engine.fit_sensor_forecast(times, values, horizon_hours=3, step_minutes=60)

    assert points is not None
    assert len(points) == 3
    last_hour = len(times) - 1
    for i, p in enumerate(points, start=1):
        assert p["value"] == pytest.approx(true_value(last_hour + i), abs=0.05)


def test_fit_sensor_forecast_points_land_after_the_last_observation():
    times = _hourly_times(30)
    values = [1.0 + 0.1 * h for h in range(30)]
    points = engine.fit_sensor_forecast(times, values, horizon_hours=2, step_minutes=30)
    assert points is not None
    assert datetime.fromisoformat(points[0]["time"]) > times[-1]
    assert len(points) == 4  # 2h horizon / 30min steps


# --- fit_front ----------------------------------------------------------------

def test_fit_front_returns_none_with_too_few_points():
    start = datetime(2026, 7, 20, tzinfo=timezone.utc)
    seq = [(start + timedelta(minutes=i), 13.0, 80.2) for i in range(3)]
    assert engine.fit_front(seq) is None


def test_fit_front_returns_none_when_reports_do_not_move():
    start = datetime(2026, 7, 20, tzinfo=timezone.utc)
    seq = [(start + timedelta(minutes=30 * i), 13.05, 80.28) for i in range(6)]
    assert engine.fit_front(seq) is None


def test_fit_front_returns_none_with_no_elapsed_time():
    t = datetime(2026, 7, 20, tzinfo=timezone.utc)
    seq = [(t, 13.0 + 0.001 * i, 80.2) for i in range(5)]
    assert engine.fit_front(seq) is None


def test_fit_front_recovers_a_known_northward_velocity():
    start = datetime(2026, 7, 20, tzinfo=timezone.utc)
    lat0, lon0 = 13.00, 80.25
    speed_kmh = 2.0
    seq = []
    for i in range(6):
        hours = i * 0.5
        dlat = (speed_kmh * hours) / 110.574
        seq.append((start + timedelta(hours=hours), lat0 + dlat, lon0))

    front = engine.fit_front(seq)
    assert front is not None
    assert front.speed_kmh == pytest.approx(speed_kmh, rel=0.05)
    assert front.bearing_deg <= 2.0 or front.bearing_deg >= 358.0  # due north, mod 360


def test_fit_front_sorts_an_out_of_order_sequence():
    start = datetime(2026, 7, 20, tzinfo=timezone.utc)
    lat0, lon0 = 13.00, 80.25
    speed_kmh = 3.0
    ordered = []
    for i in range(6):
        hours = i * 0.5
        dlat = (speed_kmh * hours) / 110.574
        ordered.append((start + timedelta(hours=hours), lat0 + dlat, lon0))
    shuffled = list(reversed(ordered))
    front = engine.fit_front(shuffled)
    assert front is not None
    assert front.speed_kmh == pytest.approx(speed_kmh, rel=0.05)


# --- project_front_cells -----------------------------------------------------

def test_project_front_cells_empty_with_no_current_cells():
    front = engine.Front(vx_kmh=0.0, vy_kmh=1.0, speed_kmh=1.0, bearing_deg=0.0, lat0=13.0)
    assert engine.project_front_cells(front, set(), 1.0) == set()


def test_project_front_cells_moves_north_for_a_northward_front():
    lat0, lon0 = 13.00, 80.25
    cell = h3.latlng_to_cell(lat0, lon0, 8)
    origin_lat, origin_lon = h3.cell_to_latlng(cell)
    front = engine.Front(vx_kmh=0.0, vy_kmh=50.0, speed_kmh=50.0, bearing_deg=0.0, lat0=origin_lat)

    projected = engine.project_front_cells(front, {cell}, hours_ahead=1.0, resolution=8)

    assert projected != {cell}
    proj_lat, _ = h3.cell_to_latlng(next(iter(projected)))
    assert proj_lat > origin_lat


def test_project_front_cells_keeps_cells_included_when_hours_ahead_is_zero():
    lat0, lon0 = 13.00, 80.25
    cell = h3.latlng_to_cell(lat0, lon0, 8)
    front = engine.Front(vx_kmh=10.0, vy_kmh=10.0, speed_kmh=math.sqrt(200), bearing_deg=45.0, lat0=lat0)
    assert engine.project_front_cells(front, {cell}, hours_ahead=0.0, resolution=8) == {cell}
