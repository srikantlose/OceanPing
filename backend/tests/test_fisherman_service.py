from datetime import datetime, timezone
from types import SimpleNamespace

from app.modules.fisherman import service
from app.modules.ingest.service import FISHERMAN_START_TRUST


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


# --- register_fisherman --------------------------------------------------------

class _ReporterDb:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.committed = False

    def scalar(self, stmt):
        return self.existing

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.committed = True


def test_register_fisherman_new_reporter_gets_elevated_trust():
    db = _ReporterDb(existing=None)
    reporter, cooperative = service.register_fisherman(db, "telegram", "42", "+919840012345")
    assert cooperative == "Kasimedu Fishermen Welfare Cooperative"
    assert reporter.role == "fisherman"
    assert reporter.trust_score == FISHERMAN_START_TRUST
    assert db.committed


def test_register_fisherman_unknown_phone_returns_none():
    db = _ReporterDb()
    reporter, cooperative = service.register_fisherman(db, "telegram", "42", "+910000000000")
    assert reporter is None and cooperative is None
    assert db.added == []
    assert not db.committed


def test_register_fisherman_does_not_override_earned_trust():
    existing = SimpleNamespace(role="citizen", trust_score=0.3, verified_count=0, debunked_count=2)
    db = _ReporterDb(existing=existing)
    reporter, cooperative = service.register_fisherman(db, "telegram", "42", "+919840012345")
    assert reporter.role == "fisherman"
    assert reporter.trust_score == 0.3  # earned (debunked) history is left alone
    assert cooperative == "Kasimedu Fishermen Welfare Cooperative"


def test_register_fisherman_idempotent_for_already_registered_member():
    existing = SimpleNamespace(
        role="fisherman", trust_score=FISHERMAN_START_TRUST, verified_count=0, debunked_count=0
    )
    db = _ReporterDb(existing=existing)
    reporter, _ = service.register_fisherman(db, "telegram", "42", "+919840012345")
    assert reporter.role == "fisherman"
    assert reporter.trust_score == FISHERMAN_START_TRUST


# --- PFZ advisories --------------------------------------------------------------

class _PfzDb:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []
        self.added = []
        self.committed = False
        self.deleted = False

    def query(self, model):
        return self

    def filter(self, *a, **k):
        return self

    def delete(self):
        n = len(self.rows)
        self.rows = []
        self.deleted = True
        return n

    def add(self, obj):
        self.added.append(obj)
        self.rows.append(obj)

    def scalars(self, stmt):
        return _FakeScalars(self.rows)

    def commit(self):
        self.committed = True


def test_refresh_pfz_advisories_replaces_existing_batch():
    db = _PfzDb(rows=[SimpleNamespace()])  # a stale row from a previous refresh
    count = service.refresh_pfz_advisories(db, sector="Test Sector")
    assert count == 3  # one zone per pfz.LANDING_SITES entry
    assert db.deleted
    assert db.committed
    assert len(db.added) == 3
    assert all(row.sector == "Test Sector" for row in db.added)
    assert all(row.source == "stub" for row in db.added)


def test_active_pfz_advisories_serializes_rows():
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(
        lat=13.1, lon=80.4, depth_m=45.0, distance_km=30.0,
        bearing="30.0 km NE of Kasimedu", valid_until=now,
    )
    db = _PfzDb(rows=[row])
    result = service.active_pfz_advisories(db, sector="Test Sector")
    assert result == [{
        "lat": 13.1, "lon": 80.4, "depth_m": 45.0, "distance_km": 30.0,
        "bearing": "30.0 km NE of Kasimedu", "valid_until": now.isoformat(),
    }]


# --- sea-state -------------------------------------------------------------------

class _StationsDb:
    def __init__(self, stations):
        self.stations = stations

    def scalars(self, stmt):
        return _FakeScalars(self.stations)


class _SensorDb:
    def __init__(self, readings, anomalies):
        self._readings = readings
        self._anomalies = anomalies

    def execute(self, stmt):
        return _FakeScalars(self._readings)

    def scalars(self, stmt):
        return _FakeScalars(self._anomalies)



def test_nearest_station_picks_closest_by_distance():
    near = SimpleNamespace(id="near", name="Near Station", lat=13.06, lon=80.30)
    far = SimpleNamespace(id="far", name="Far Station", lat=37.755, lon=-122.839)
    db = _StationsDb([far, near])
    result = service._nearest_station(db, 13.05, 80.2824)
    assert result.id == "near"


def test_nearest_station_returns_none_without_any_station():
    assert service._nearest_station(_StationsDb([]), 13.05, 80.28) is None


def test_station_sensor_summary_includes_latest_and_anomalies():
    now = datetime.now(timezone.utc)
    db = _SensorDb([("wave_height", 1.2, now)], [SimpleNamespace(variable="wave_height", zscore=3.456)])
    summary = service._station_sensor_summary(db, "ndbc-46026")
    assert summary["latest"]["wave_height"] == {"value": 1.2, "time": now.isoformat()}
    assert summary["anomalies"] == [{"variable": "wave_height", "zscore": 3.46}]


def test_nearest_station_reading_flags_local_station(monkeypatch):
    station = SimpleNamespace(id="s1", name="Test Station", lat=13.06, lon=80.30)
    monkeypatch.setattr(service, "_nearest_station", lambda db, lat, lon: station)
    monkeypatch.setattr(service, "_station_sensor_summary", lambda db, station_id: {"latest": {}, "anomalies": []})
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(instrument_radius_km=25.0))
    result = service.nearest_station_reading(object(), 13.05, 80.2824)
    assert result["station_id"] == "s1"
    assert result["is_local"] is True


def test_nearest_station_reading_flags_far_station_as_not_local(monkeypatch):
    station = SimpleNamespace(id="sf", name="SF Buoy", lat=37.755, lon=-122.839)
    monkeypatch.setattr(service, "_nearest_station", lambda db, lat, lon: station)
    monkeypatch.setattr(service, "_station_sensor_summary", lambda db, station_id: {"latest": {}, "anomalies": []})
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(instrument_radius_km=25.0))
    result = service.nearest_station_reading(object(), 13.05, 80.2824)
    assert result["is_local"] is False


def test_nearest_station_reading_returns_none_without_any_station(monkeypatch):
    monkeypatch.setattr(service, "_nearest_station", lambda db, lat, lon: None)
    assert service.nearest_station_reading(object()) is None


def test_nearest_station_reading_defaults_to_pilot_centroid(monkeypatch):
    captured = {}

    def fake_nearest(db, lat, lon):
        captured["lat"], captured["lon"] = lat, lon
        return None

    monkeypatch.setattr(service, "_nearest_station", fake_nearest)
    service.nearest_station_reading(object())
    assert (captured["lat"], captured["lon"]) == service.PILOT_CENTROID
