from datetime import datetime, timezone

from app.modules.satellite import service
from app.modules.satellite.providers import ScanResult


class _FakeIncident:
    def __init__(self, id, hazard_type):
        self.id = id
        self.hazard_type = hazard_type


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, incidents):
        self._incidents = incidents
        self.added = []
        self.committed = False

    def scalars(self, stmt):
        return _FakeScalars(self._incidents)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True


class _FakeProvider:
    name = "fake"

    def __init__(self, score=0.6, raise_exc=False):
        self.score = score
        self.raise_exc = raise_exc

    def observe(self, incident, recipe):
        if self.raise_exc:
            raise RuntimeError("boom")
        return ScanResult(
            provider=self.name, recipe=recipe, score=self.score,
            scene_time=datetime.now(timezone.utc), scene_url="fake://x",
        )


class _NoneProvider:
    name = "none"

    def observe(self, incident, recipe):
        return None


def test_poll_satellite_skips_hazards_without_a_recipe(monkeypatch):
    db = _FakeDb([_FakeIncident("i1", "tsunami")])  # tsunami has no satellite recipe
    monkeypatch.setattr(service, "get_provider", lambda: _FakeProvider())
    inserted = service.poll_satellite(db)
    assert inserted == 0
    assert db.added == []
    assert db.committed


def test_poll_satellite_inserts_observation_for_recipe_hazard(monkeypatch):
    db = _FakeDb([_FakeIncident("i1", "oil_spill")])
    monkeypatch.setattr(service, "get_provider", lambda: _FakeProvider(score=0.8))
    inserted = service.poll_satellite(db)
    assert inserted == 1
    row = db.added[0]
    assert row.incident_id == "i1"
    assert row.recipe == "sentinel1_sar_dark_slick"
    assert row.score == 0.8
    assert db.committed


def test_poll_satellite_survives_provider_exception(monkeypatch):
    db = _FakeDb([_FakeIncident("i1", "oil_spill"), _FakeIncident("i2", "algal_bloom")])
    monkeypatch.setattr(service, "get_provider", lambda: _FakeProvider(raise_exc=True))
    inserted = service.poll_satellite(db)
    assert inserted == 0
    assert db.added == []
    assert db.committed  # still commits even though every provider call failed


def test_poll_satellite_skips_none_observation(monkeypatch):
    db = _FakeDb([_FakeIncident("i1", "coastal_flooding")])
    monkeypatch.setattr(service, "get_provider", lambda: _NoneProvider())
    inserted = service.poll_satellite(db)
    assert inserted == 0
    assert db.added == []
