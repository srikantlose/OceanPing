from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.modules.scoring import engine, service
from app.modules.scoring.service import apply_verification


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self, stmt):
        return _FakeScalars(self._rows)


class _FakeSatelliteRow:
    def __init__(self, provider, recipe, score, scene_time, scene_url):
        self.provider = provider
        self.recipe = recipe
        self.score = score
        self.scene_time = scene_time
        self.scene_url = scene_url


class _FakeReport:
    def __init__(self, incident_id, hazard_type):
        self.incident_id = incident_id
        self.hazard_type = hazard_type


def test_satellite_observations_empty_without_incident():
    report = _FakeReport(None, "oil_spill")
    assert service._satellite_observations(db=_FakeDb([]), report=report) == []


def test_satellite_observations_empty_for_hazard_without_recipe():
    # tsunami has no satellite recipe — satellite must never gate a fast hazard.
    report = _FakeReport("inc-1", "tsunami")
    assert service._satellite_observations(db=_FakeDb([]), report=report) == []


def test_satellite_observations_serializes_rows():
    now = datetime.now(timezone.utc)
    row = _FakeSatelliteRow("stub", "sentinel1_sar_dark_slick", 0.712345, now, "stub://x")
    report = _FakeReport("inc-1", "oil_spill")
    result = service._satellite_observations(db=_FakeDb([row]), report=report)
    assert result == [{
        "provider": "stub",
        "recipe": "sentinel1_sar_dark_slick",
        "score": 0.7123,
        "scene_time": now.isoformat(),
        "scene_url": "stub://x",
    }]


class _FakeReportForOfficial:
    def __init__(self, hazard_type, lat, lon):
        self.hazard_type = hazard_type
        self.lat = lat
        self.lon = lon


def test_official_advisory_returns_none_without_a_match(monkeypatch):
    monkeypatch.setattr(service, "official_advisory_for", lambda db, hazard_type, lat, lon: None)
    report = _FakeReportForOfficial("tsunami", 13.05, 80.28)
    assert service._official_advisory(db=None, report=report) is None


def test_official_advisory_serializes_the_matched_row(monkeypatch):
    advisory = SimpleNamespace(
        id="adv-1", sender="alerts@imd.gov.in", event="Tsunami Warning",
        severity="Severe", certainty="Observed",
    )
    monkeypatch.setattr(service, "official_advisory_for", lambda db, hazard_type, lat, lon: advisory)
    report = _FakeReportForOfficial("tsunami", 13.05, 80.28)
    result = service._official_advisory(db=None, report=report)
    assert result == {
        "id": "adv-1",
        "sender": "alerts@imd.gov.in",
        "event": "Tsunami Warning",
        "severity": "Severe",
        "certainty": "Observed",
    }


class _FakeReporter:
    def __init__(self, created_at, external_id_hash="hash123"):
        self.created_at = created_at
        self.external_id_hash = external_id_hash


class _FakeReportForAccount:
    def __init__(self, created_at, reporter):
        self.created_at = created_at
        self.reporter = reporter


def test_account_device_score_uses_redis_burst_count(monkeypatch):
    now = datetime.now(timezone.utc)
    reporter = _FakeReporter(created_at=now - timedelta(hours=24 * 7))
    report = _FakeReportForAccount(created_at=now, reporter=reporter)

    class _FakeRedis:
        def get(self, key):
            assert key == "rl:rep:hash123"
            return "3"

    monkeypatch.setattr(service, "get_redis", lambda: _FakeRedis())
    assert service._account_device_score(report) == engine.account_device_score(24 * 7, 3)


def test_account_device_score_falls_back_when_redis_unavailable(monkeypatch):
    now = datetime.now(timezone.utc)
    reporter = _FakeReporter(created_at=now)
    report = _FakeReportForAccount(created_at=now, reporter=reporter)

    def _boom():
        raise RuntimeError("no redis")

    monkeypatch.setattr(service, "get_redis", _boom)
    assert service._account_device_score(report) == engine.account_device_score(0, 1)


def test_apply_verification_rejects_unknown_action():
    with pytest.raises(ValueError):
        apply_verification(db=None, report=object(), analyst="a", action="ignore")


def test_apply_verification_rejects_unknown_corrected_hazard_type():
    # Validation happens before any report/db access, so bare stand-ins are fine here.
    with pytest.raises(ValueError):
        apply_verification(
            db=None,
            report=object(),
            analyst="a",
            action="reject",
            corrected_hazard_type="not_a_real_hazard_type",
        )
