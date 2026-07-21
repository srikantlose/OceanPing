from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.modules.forecast import engine, service

P_START = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
PAST = datetime(2020, 1, 1, tzinfo=timezone.utc)
FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Db:
    """Fake DB double supporting execute()/scalars() as queues popped in call
    order, plus add()/flush()/commit()/get() — same style as
    test_sitrep_service.py's fakes, generalized to cover both query styles
    this module uses."""

    def __init__(self):
        self._execute_queue: list[list] = []
        self._scalars_queue: list[list] = []
        self._get_map: dict = {}
        self.added: list = []
        self.committed = False

    def queue_execute(self, rows):
        self._execute_queue.append(rows)

    def queue_scalars(self, rows):
        self._scalars_queue.append(rows)

    def execute(self, stmt):
        return _Rows(self._execute_queue.pop(0))

    def scalars(self, stmt):
        return _Rows(self._scalars_queue.pop(0))

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def get(self, model, pk):
        return self._get_map.get(pk)

    def commit(self):
        self.committed = True


def _report(created_at, lat, lon):
    return SimpleNamespace(created_at=created_at, lat=lat, lon=lon)


# --- nearest_pilot_location ---------------------------------------------------

def test_nearest_pilot_location_picks_closest_named_landmark():
    assert service.nearest_pilot_location(13.051, 80.283) == "Marina Beach"


# --- generate_sensor_forecast --------------------------------------------------

def _sensor_settings():
    return SimpleNamespace(
        forecast_sensor_baseline_days=7.0,
        forecast_sensor_horizon_hours=3.0,
        forecast_sensor_step_minutes=30,
    )


def test_generate_sensor_forecast_returns_none_with_insufficient_history(monkeypatch):
    monkeypatch.setattr(service, "get_settings", _sensor_settings)
    db = _Db()
    db.queue_execute([])
    assert service.generate_sensor_forecast(db, "ndbc-46026", "wave_height") is None


def test_generate_sensor_forecast_builds_and_audits_a_forecast(monkeypatch):
    monkeypatch.setattr(service, "get_settings", _sensor_settings)
    captured = {}
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: captured.update(kw))
    monkeypatch.setattr(
        service.engine, "fit_sensor_forecast",
        lambda times, values, horizon_hours, step_minutes: [{"time": "t", "value": 1.0}],
    )
    db = _Db()
    db.queue_execute([(P_START, 1.0)])

    forecast = service.generate_sensor_forecast(db, "ndbc-46026", "wave_height")

    assert forecast.kind == "sensor"
    assert forecast.subject_type == "station"
    assert forecast.subject_id == "ndbc-46026"
    assert forecast.content == {"variable": "wave_height", "points": [{"time": "t", "value": 1.0}], "model": "harmonic_trend_v1"}
    assert db.added == [forecast]
    assert captured["payload"]["variable"] == "wave_height"
    assert captured["payload"]["forecast_id"] == str(forecast.id)


def test_generate_sensor_forecasts_counts_successful_stations(monkeypatch):
    monkeypatch.setattr(service, "load_station_configs", lambda: [
        {"id": "s1", "variables": [{"label": "wave_height"}, {"label": "water_temp"}]},
        {"id": "s2", "variables": [{"label": "water_level"}]},
    ])
    results = iter([SimpleNamespace(), None, SimpleNamespace()])
    monkeypatch.setattr(service, "generate_sensor_forecast", lambda db, sid, var: next(results))

    db = _Db()
    n = service.generate_sensor_forecasts(db)

    assert n == 2
    assert db.committed


# --- latest_sensor_forecast_point / station_forecast_series --------------------

def test_latest_sensor_forecast_point_picks_nearest_to_horizon():
    forecast = SimpleNamespace(
        generated_at=P_START,
        content={"variable": "water_level", "points": [
            {"time": (P_START + timedelta(hours=1)).isoformat(), "value": 1.5},
            {"time": (P_START + timedelta(hours=2)).isoformat(), "value": 2.0},
        ]},
    )
    db = _Db()
    db.queue_scalars([forecast])
    point = service.latest_sensor_forecast_point(db, "water_level", hours_ahead=2.1)
    assert point["value"] == 2.0


def test_latest_sensor_forecast_point_none_when_variable_not_forecast():
    forecast = SimpleNamespace(generated_at=P_START, content={"variable": "wave_height", "points": [{"time": P_START.isoformat(), "value": 1.0}]})
    db = _Db()
    db.queue_scalars([forecast])
    assert service.latest_sensor_forecast_point(db, "water_level", 1.0) is None


def test_station_forecast_series_keeps_freshest_per_variable():
    newer = SimpleNamespace(content={"variable": "wave_height", "points": [{"time": "t2", "value": 2.0}]})
    older = SimpleNamespace(content={"variable": "wave_height", "points": [{"time": "t1", "value": 1.0}]})
    db = _Db()
    db.queue_scalars([newer, older])  # already ordered newest-first, like the real query
    series = service.station_forecast_series(db, "ndbc-46026")
    assert series == {"wave_height": [["t2", 2.0]]}


# --- generate_propagation_forecast ---------------------------------------------

def test_incident_report_sequence_sorts_by_created_at():
    r1 = _report(P_START + timedelta(hours=1), 13.1, 80.3)
    r2 = _report(P_START, 13.0, 80.2)
    incident = SimpleNamespace(reports=[r1, r2])
    assert service._incident_report_sequence(incident) == [
        (P_START, 13.0, 80.2),
        (P_START + timedelta(hours=1), 13.1, 80.3),
    ]


def test_generate_propagation_forecast_returns_none_without_a_fittable_front(monkeypatch):
    monkeypatch.setattr(service.engine, "fit_front", lambda seq: None)
    incident = SimpleNamespace(
        reports=[_report(P_START, 13.0, 80.2)], h3_cells=[], hazard_type="coastal_flooding",
        centroid_lat=13.0, centroid_lon=80.2, id="inc-1",
    )
    assert service.generate_propagation_forecast(_Db(), incident) is None


def test_generate_propagation_forecast_builds_projected_cells_and_audits(monkeypatch):
    front = engine.Front(vx_kmh=0.0, vy_kmh=5.0, speed_kmh=5.0, bearing_deg=0.0, lat0=13.0)
    monkeypatch.setattr(service.engine, "fit_front", lambda seq: front)
    monkeypatch.setattr(service.engine, "project_front_cells", lambda fr, cells, h: {"cellX"})
    monkeypatch.setattr(service, "nearest_pilot_location", lambda lat, lon: "Marina Beach")
    captured = {}
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: captured.update(kw))
    incident = SimpleNamespace(
        reports=[_report(P_START, 13.0, 80.2)], h3_cells=["cellA"], hazard_type="coastal_flooding",
        centroid_lat=13.0, centroid_lon=80.2, id="inc-1",
    )
    db = _Db()

    forecast = service.generate_propagation_forecast(db, incident)

    assert forecast.kind == "propagation"
    assert forecast.hazard_type == "coastal_flooding"
    assert forecast.content["location"] == "Marina Beach"
    assert forecast.content["front"] == {"speed_kmh": 5.0, "bearing_deg": 0.0}
    for h in service.PROPAGATION_HORIZONS_HOURS:
        assert forecast.content["projected"][str(h)] == ["cellX"]
    assert db.added == [forecast]
    assert captured["payload"]["speed_kmh"] == 5.0


def test_generate_propagation_forecasts_counts_successful_incidents(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(forecast_propagation_incident_hours=6.0))
    incidents = [SimpleNamespace(id="i1"), SimpleNamespace(id="i2")]
    results = iter([None, SimpleNamespace()])
    monkeypatch.setattr(service, "generate_propagation_forecast", lambda db, inc: next(results))
    db = _Db()
    db.queue_scalars(incidents)

    n = service.generate_propagation_forecasts(db)

    assert n == 1
    assert db.committed


# --- latest_projected_cells ----------------------------------------------------

def test_latest_projected_cells_returns_nearest_horizon():
    forecast = SimpleNamespace(content={"projected": {"1.0": ["cellA"], "2.0": ["cellB"], "3.0": ["cellC"]}})
    db = _Db()
    db.queue_scalars([forecast])
    assert service.latest_projected_cells(db, "inc-1") == ["cellA"]


def test_latest_projected_cells_empty_when_no_recent_forecast():
    db = _Db()
    db.queue_scalars([])
    assert service.latest_projected_cells(db, "inc-1") == []


# --- _validate_sensor_forecast --------------------------------------------------

def test_validate_sensor_forecast_none_before_horizon_elapses():
    forecast = SimpleNamespace(content={"points": [{"time": FUTURE.isoformat(), "value": 1.0}], "variable": "water_level"}, subject_id="s1")
    assert service._validate_sensor_forecast(_Db(), forecast) is None


def test_validate_sensor_forecast_scores_mean_abs_error():
    t1, t2 = PAST, PAST + timedelta(hours=1)
    forecast = SimpleNamespace(
        content={"variable": "water_level", "points": [
            {"time": t1.isoformat(), "value": 1.0},
            {"time": t2.isoformat(), "value": 1.5},
        ]},
        subject_id="s1",
    )
    db = _Db()
    db.queue_execute([(t1, 1.2)])
    db.queue_execute([(t2, 1.3)])

    result = service._validate_sensor_forecast(db, forecast)

    assert result["scored_points"] == 2
    assert result["mean_abs_error"] == pytest.approx(0.2, abs=1e-6)


def test_validate_sensor_forecast_none_scored_when_no_matching_readings():
    forecast = SimpleNamespace(content={"variable": "water_level", "points": [{"time": PAST.isoformat(), "value": 1.0}]}, subject_id="s1")
    db = _Db()
    db.queue_execute([])
    result = service._validate_sensor_forecast(db, forecast)
    assert result == {"scored_points": 0, "mean_abs_error": None}


# --- _validate_propagation_forecast ---------------------------------------------

def test_validate_propagation_forecast_none_before_furthest_horizon_elapses():
    forecast = SimpleNamespace(content={"projected": {"1.0": ["cellA"]}}, generated_at=FUTURE)
    assert service._validate_propagation_forecast(_Db(), forecast) is None


def test_validate_propagation_forecast_none_when_nothing_was_projected():
    forecast = SimpleNamespace(content={"projected": {}}, generated_at=PAST)
    assert service._validate_propagation_forecast(_Db(), forecast) is None


def test_validate_propagation_forecast_computes_hit_rate():
    forecast = SimpleNamespace(content={"projected": {"1.0": ["cellA", "cellB"]}}, generated_at=PAST)
    hit_report = SimpleNamespace(h3_cell="cellA")
    db = _Db()
    db.queue_scalars([hit_report])

    result = service._validate_propagation_forecast(db, forecast)

    assert result["per_horizon"]["1.0"] == {"cells": 2, "hit_cells": 1}
    assert result["hit_rate"] == 0.5


# --- validate_forecasts ---------------------------------------------------------

def test_validate_forecasts_scores_ready_ones_and_leaves_others_pending(monkeypatch):
    ready = SimpleNamespace(kind="sensor", validated_at=None, validation=None)
    not_ready = SimpleNamespace(kind="propagation", validated_at=None, validation=None)
    monkeypatch.setattr(service, "_validate_sensor_forecast", lambda db, f: {"mean_abs_error": 0.1} if f is ready else None)
    monkeypatch.setattr(service, "_validate_propagation_forecast", lambda db, f: None)
    db = _Db()
    db.queue_scalars([ready, not_ready])

    n = service.validate_forecasts(db)

    assert n == 1
    assert ready.validation == {"mean_abs_error": 0.1}
    assert ready.validated_at is not None
    assert not_ready.validation is None
    assert not_ready.validated_at is None
    assert db.committed


# --- accuracy_summary ------------------------------------------------------------

def test_accuracy_summary_aggregates_by_nearest_location(monkeypatch):
    monkeypatch.setattr(service, "nearest_pilot_location", lambda lat, lon: "Marina Beach")
    station = SimpleNamespace(lat=13.05, lon=80.28)
    sensor_f = SimpleNamespace(
        kind="sensor", validated_at=P_START, validation={"mean_abs_error": 0.2},
        content={"variable": "water_level"}, subject_id="s1",
    )
    unscored_sensor = SimpleNamespace(
        kind="sensor", validated_at=P_START, validation={"mean_abs_error": None},
        content={"variable": "water_level"}, subject_id="s1",
    )
    prop_f = SimpleNamespace(
        kind="propagation", validated_at=P_START, validation={"hit_rate": 0.5},
        content={"location": "Marina Beach"}, hazard_type="coastal_flooding",
    )
    db = _Db()
    db.queue_scalars([sensor_f, unscored_sensor, prop_f])
    db._get_map = {"s1": station}

    out = service.accuracy_summary(db)

    assert out["sensor"] == [{"location": "Marina Beach", "variable": "water_level", "n_forecasts": 1, "mean_abs_error": 0.2}]
    assert out["propagation"] == [{"location": "Marina Beach", "hazard_type": "coastal_flooding", "n_forecasts": 1, "mean_hit_rate": 0.5}]
