from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.modules.sitrep import service

P_START = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
P_END = datetime(2026, 7, 20, 11, 0, tzinfo=timezone.utc)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _ExecDb:
    """db.execute(stmt) returns a fixed row set regardless of stmt — fine
    since each helper under test issues exactly one execute() call."""
    def __init__(self, rows):
        self._rows = rows

    def execute(self, stmt):
        return _Rows(self._rows)


class _ScalarsDb:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self, stmt):
        return _Rows(self._rows)


class _SeqScalarsDb:
    """For helpers issuing multiple .scalars() calls in sequence — pops the
    next canned result list per call, in call order."""
    def __init__(self, *result_lists):
        self._queue = list(result_lists)

    def scalars(self, stmt):
        return _Rows(self._queue.pop(0))


class _SitrepDb:
    def __init__(self, last=None):
        self._last = last
        self.added = []
        self.committed = False

    def scalars(self, stmt):
        return _Rows([self._last] if self._last is not None else [])

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.committed = True


# --- _report_counts ------------------------------------------------------------

def test_report_counts_tallies_status_and_hazard():
    rows = [
        SimpleNamespace(status="verified", hazard_type="coastal_flooding"),
        SimpleNamespace(status="corroborated", hazard_type="coastal_flooding"),
        SimpleNamespace(status="unverified", hazard_type="oil_spill"),
    ]
    out = service._report_counts(_ExecDb(rows), P_START, P_END)
    assert out == {
        "total": 3,
        "by_status": {"verified": 1, "corroborated": 1, "unverified": 1},
        "by_hazard": {"coastal_flooding": 2, "oil_spill": 1},
    }


def test_report_counts_empty_period():
    assert service._report_counts(_ExecDb([]), P_START, P_END) == {"total": 0, "by_status": {}, "by_hazard": {}}


# --- _incident_counts ------------------------------------------------------------

def test_incident_counts_splits_new_from_merely_touched():
    touched = [
        SimpleNamespace(status="corroborated", first_seen=P_START + timedelta(minutes=5), last_seen=P_END - timedelta(minutes=1)),
        SimpleNamespace(status="unverified", first_seen=P_START - timedelta(hours=3), last_seen=P_END - timedelta(minutes=10)),
    ]
    out = service._incident_counts(_ScalarsDb(touched), P_START, P_END)
    assert out == {"active_in_period": 2, "new": 1, "by_status": {"corroborated": 1, "unverified": 1}}


# --- _alerts_summary ---------------------------------------------------------------

def test_alerts_summary_separates_issued_from_active_now():
    issued_at = P_START + timedelta(minutes=20)
    issued = [SimpleNamespace(tier="watch", hazard_type="coastal_flooding", issued_by=None, created_at=issued_at)]
    active_now = [
        SimpleNamespace(tier="watch", hazard_type="coastal_flooding", issued_by=None),
        SimpleNamespace(tier="warning", hazard_type="storm_surge", issued_by="alice"),
    ]
    out = service._alerts_summary(_SeqScalarsDb(issued, active_now), P_START, P_END)
    assert out["issued"] == [
        {"tier": "watch", "hazard_type": "coastal_flooding", "issued_by": "automatic", "created_at": issued_at.isoformat()}
    ]
    assert out["active_now"] == [
        {"tier": "watch", "hazard_type": "coastal_flooding", "issued_by": "automatic"},
        {"tier": "warning", "hazard_type": "storm_surge", "issued_by": "alice"},
    ]


# --- _resources_summary --------------------------------------------------------------

def test_resources_summary_counts_open_shelters_and_known_capacity():
    shelters = [
        SimpleNamespace(status="open", capacity=200),
        SimpleNamespace(status="open", capacity=None),
        SimpleNamespace(status="full", capacity=150),
        SimpleNamespace(status="closed", capacity=100),
    ]
    out = service._resources_summary(_ScalarsDb(shelters))
    assert out == {
        "shelters_total": 4,
        "shelters_open": 2,
        "open_capacity_total": 200,
        "open_capacity_unknown_count": 1,
    }


# --- _audit_summary ------------------------------------------------------------------

def test_audit_summary_delegates_to_verify_chain(monkeypatch):
    monkeypatch.setattr(service, "verify_chain", lambda db: (True, 7))
    assert service._audit_summary(object()) == {"chain_intact": True, "entries_checked": 7}


# --- _hotspot_movement ---------------------------------------------------------------

def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def _feature(lat, lon, hazard, count=5, intensity=3.0):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"report_count": count, "dominant_hazard": hazard, "intensity": intensity, "hazards": {hazard: count}},
    }


def test_hotspot_movement_tags_new_when_no_previous(monkeypatch):
    monkeypatch.setattr(service, "compute_hotspots", lambda db: _fc([_feature(13.05, 80.28, "coastal_flooding")]))
    out = service._hotspot_movement(object(), [])
    assert out["tagged"][0]["movement"] == "new"
    assert out["cleared"] == []


def test_hotspot_movement_tags_persisting_when_matched_within_radius(monkeypatch):
    monkeypatch.setattr(service, "compute_hotspots", lambda db: _fc([_feature(13.0501, 80.2801, "coastal_flooding")]))
    previous = [{"lat": 13.05, "lon": 80.28, "dominant_hazard": "coastal_flooding", "report_count": 4, "intensity": 2.0}]
    out = service._hotspot_movement(object(), previous)
    assert out["tagged"][0]["movement"] == "persisting"
    assert out["cleared"] == []


def test_hotspot_movement_lists_cleared_hotspots_not_matched(monkeypatch):
    monkeypatch.setattr(service, "compute_hotspots", lambda db: _fc([]))
    previous = [{"lat": 13.05, "lon": 80.28, "dominant_hazard": "coastal_flooding", "report_count": 4, "intensity": 2.0}]
    out = service._hotspot_movement(object(), previous)
    assert out["current"] == []
    assert out["cleared"] == previous


def test_hotspot_movement_does_not_match_across_different_dominant_hazards(monkeypatch):
    monkeypatch.setattr(service, "compute_hotspots", lambda db: _fc([_feature(13.05, 80.28, "oil_spill")]))
    previous = [{"lat": 13.05, "lon": 80.28, "dominant_hazard": "coastal_flooding", "report_count": 4, "intensity": 2.0}]
    out = service._hotspot_movement(object(), previous)
    assert out["tagged"][0]["movement"] == "new"
    assert out["cleared"] == previous


def test_hotspot_movement_does_not_match_beyond_radius(monkeypatch):
    monkeypatch.setattr(service, "compute_hotspots", lambda db: _fc([_feature(13.20, 80.40, "coastal_flooding")]))
    previous = [{"lat": 13.05, "lon": 80.28, "dominant_hazard": "coastal_flooding", "report_count": 4, "intensity": 2.0}]
    out = service._hotspot_movement(object(), previous)
    assert out["tagged"][0]["movement"] == "new"
    assert out["cleared"] == previous


# --- build_snapshot / snapshot_hash ---------------------------------------------------

def test_build_snapshot_assembles_all_sections(monkeypatch):
    monkeypatch.setattr(service, "_report_counts", lambda db, s, e: {"total": 1})
    monkeypatch.setattr(service, "_incident_counts", lambda db, s, e: {"active_in_period": 1})
    monkeypatch.setattr(service, "_alerts_summary", lambda db, s, e: {"issued": []})
    monkeypatch.setattr(service, "_hotspot_movement", lambda db, previous: {"current": previous})
    monkeypatch.setattr(service, "_resources_summary", lambda db: {"shelters_total": 2})
    monkeypatch.setattr(service, "_audit_summary", lambda db: {"chain_intact": True})

    snapshot = service.build_snapshot(object(), P_START, P_END, previous_hotspots=["prev"])
    assert snapshot == {
        "period_start": P_START.isoformat(),
        "period_end": P_END.isoformat(),
        "reports": {"total": 1},
        "incidents": {"active_in_period": 1},
        "alerts": {"issued": []},
        "hotspots": {"current": ["prev"]},
        "resources": {"shelters_total": 2},
        "audit": {"chain_intact": True},
    }


def test_snapshot_hash_is_deterministic_and_key_order_independent():
    assert service.snapshot_hash({"b": 1, "a": 2}) == service.snapshot_hash({"a": 2, "b": 1})


def test_snapshot_hash_changes_when_data_changes():
    assert service.snapshot_hash({"a": 1}) != service.snapshot_hash({"a": 2})


# --- generate_sitrep / file_sitrep ----------------------------------------------------

def _stub_snapshot_pipeline(monkeypatch, captured=None):
    def fake_build_snapshot(db, start, end, previous_hotspots=None):
        if captured is not None:
            captured["start"] = start
            captured["previous_hotspots"] = previous_hotspots
        return {"period_start": start.isoformat(), "period_end": end.isoformat()}

    monkeypatch.setattr(service, "build_snapshot", fake_build_snapshot)
    monkeypatch.setattr(service.engine, "build_sitrep", lambda snapshot: {"title": "t", "summary": "s", "sections": {}})
    monkeypatch.setattr(service, "snapshot_hash", lambda snapshot: "deadbeef")
    monkeypatch.setattr(service, "append_audit", lambda *a, **k: None)


def test_generate_sitrep_uses_configured_period_when_no_prior_sitrep(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(sitrep_period_hours=1.0))
    _stub_snapshot_pipeline(monkeypatch)

    db = _SitrepDb(last=None)
    sitrep = service.generate_sitrep(db)

    assert sitrep.status == "draft"
    assert sitrep.data_snapshot_hash == "deadbeef"
    assert (sitrep.period_end - sitrep.period_start).total_seconds() == pytest.approx(3600, abs=1)
    assert db.committed
    assert db.added == [sitrep]


def test_generate_sitrep_anchors_to_previous_period_end_and_carries_its_hotspots(monkeypatch):
    prev_end = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)
    last = SimpleNamespace(period_end=prev_end, content={"sections": {"hotspots": {"current": ["prev_hotspot"]}}})
    captured = {}
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(sitrep_period_hours=1.0))
    _stub_snapshot_pipeline(monkeypatch, captured)

    sitrep = service.generate_sitrep(_SitrepDb(last=last))

    assert sitrep.period_start == prev_end
    assert captured["previous_hotspots"] == ["prev_hotspot"]


def test_file_sitrep_marks_filed_and_records_analyst(monkeypatch):
    monkeypatch.setattr(service, "append_audit", lambda *a, **k: None)
    sitrep = SimpleNamespace(id="sitrep-1", status="draft", data_snapshot_hash="abc", filed_by=None, filed_at=None)
    db = _SitrepDb()
    out = service.file_sitrep(db, sitrep, analyst="alice")
    assert out.status == "filed"
    assert out.filed_by == "alice"
    assert out.filed_at is not None
    assert db.committed


def test_file_sitrep_rejects_an_already_filed_sitrep():
    sitrep = SimpleNamespace(status="filed", data_snapshot_hash="abc")
    with pytest.raises(ValueError):
        service.file_sitrep(_SitrepDb(), sitrep, analyst="alice")
