from datetime import datetime, timezone

import pytest

from app.models import Station
from app.modules.iot import service
from app.modules.iot.parser import IotMessageError, Reading, Telemetry

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


class _Db:
    """Minimal fake session — same shape as test_safety_service.py's, plus a
    get() keyed on a preloaded station map."""

    def __init__(self, existing=None):
        self._store = {existing.id: existing} if existing else {}
        self.added = []
        self.committed = False

    def get(self, model, pk):
        return self._store.get(pk)

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, Station):
            self._store[obj.id] = obj

    def flush(self):
        pass

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


def _tel(lat=13.21, lon=80.32, name="Ennore buoy", readings=None):
    return Telemetry(
        node_id="buoy-01",
        station_id="iot-buoy-01",
        name=name,
        lat=lat,
        lon=lon,
        readings=readings or [Reading("water_level", 1.34, NOW)],
    )


def _patch(monkeypatch, inserted=1):
    captured = {"readings": None, "audit": []}

    def fake_insert(db, station_id, rows):
        captured["readings"] = (station_id, rows)
        return inserted

    monkeypatch.setattr(service, "insert_readings", fake_insert)
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: captured["audit"].append(kw))
    return captured


# --- first registration -----------------------------------------------------


def test_first_message_registers_a_station_with_iot_provider(monkeypatch):
    captured = _patch(monkeypatch)
    db = _Db()

    result = service.ingest_telemetry(db, _tel(), now=NOW)

    assert result["first_seen"] is True
    assert result["inserted"] == 1
    station = db._store["iot-buoy-01"]
    assert station.provider == "iot"
    assert station.lat == 13.21 and station.lon == 80.32
    assert station.name == "Ennore buoy"
    assert station.variables == ["water_level"]
    assert station.last_polled_at == NOW
    assert db.committed


def test_first_registration_is_audited(monkeypatch):
    captured = _patch(monkeypatch)
    service.ingest_telemetry(_Db(), _tel(), now=NOW)
    assert captured["audit"], "first registration should be audit-logged"
    entry = captured["audit"][0]
    assert entry["event_type"] == "iot.node_registered"
    assert entry["subject_id"] == "iot-buoy-01"
    assert entry["payload"]["node_id"] == "buoy-01"


def test_first_message_without_location_is_rejected(monkeypatch):
    _patch(monkeypatch)
    with pytest.raises(IotMessageError):
        service.ingest_telemetry(_Db(), _tel(lat=None, lon=None), now=NOW)


# --- subsequent messages ----------------------------------------------------


def test_known_node_updates_liveness_without_re_registering(monkeypatch):
    captured = _patch(monkeypatch)
    existing = Station(
        id="iot-buoy-01", name="Ennore buoy", provider="iot", lat=13.21, lon=80.32,
        variables=["water_level"],
    )
    db = _Db(existing=existing)

    result = service.ingest_telemetry(db, _tel(name=None), now=NOW)

    assert result["first_seen"] is False
    assert captured["audit"] == []  # no re-registration audit
    assert existing.last_polled_at == NOW


def test_known_node_can_add_a_new_variable(monkeypatch):
    _patch(monkeypatch)
    existing = Station(
        id="iot-buoy-01", name="Ennore buoy", provider="iot", lat=13.21, lon=80.32,
        variables=["water_level"],
    )
    db = _Db(existing=existing)

    service.ingest_telemetry(
        db, _tel(readings=[Reading("wave_height", 0.9, NOW)]), now=NOW
    )
    assert existing.variables == ["water_level", "wave_height"]


def test_drifting_node_position_is_updated(monkeypatch):
    _patch(monkeypatch)
    existing = Station(
        id="iot-buoy-01", name="Ennore buoy", provider="iot", lat=13.21, lon=80.32,
        variables=["water_level"],
    )
    db = _Db(existing=existing)

    service.ingest_telemetry(db, _tel(lat=13.25, lon=80.35), now=NOW)
    assert existing.lat == 13.25 and existing.lon == 80.35


def test_readings_are_forwarded_to_the_shared_sensor_path(monkeypatch):
    captured = _patch(monkeypatch)
    tel = _tel(readings=[Reading("water_level", 2.6, NOW), Reading("wave_height", 1.1, NOW)])
    service.ingest_telemetry(_Db(), tel, now=NOW)

    station_id, rows = captured["readings"]
    assert station_id == "iot-buoy-01"
    assert {r["variable"] for r in rows} == {"water_level", "wave_height"}
    assert all(r["time"] == NOW for r in rows)
