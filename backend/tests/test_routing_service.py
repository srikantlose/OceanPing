from types import SimpleNamespace

from app.modules.geo.h3utils import cell_for
from app.modules.routing import service
from app.modules.routing.client import RoutingUnavailable

_CELL_A = cell_for(13.05, 80.28)
_CELL_B = cell_for(13.10, 80.30)
_CELL_C = cell_for(13.12, 80.29)


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


# --- shelter CRUD ------------------------------------------------------------

class _ShelterDb:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.added = []
        self.deleted = []
        self.committed = False

    def scalars(self, stmt):
        return _FakeScalars(self.rows)

    def add(self, obj):
        self.added.append(obj)

    def delete(self, obj):
        self.deleted.append(obj)

    def commit(self):
        self.committed = True


def test_list_shelters_returns_all_rows():
    rows = [SimpleNamespace(name="A"), SimpleNamespace(name="B")]
    assert service.list_shelters(_ShelterDb(rows)) == rows


def test_create_shelter_sets_geom_and_commits():
    db = _ShelterDb()
    shelter = service.create_shelter(db, name="Test Shelter", lat=13.05, lon=80.28, capacity=100)
    assert db.added == [shelter]
    assert db.committed
    assert shelter.name == "Test Shelter"
    assert shelter.geom == "SRID=4326;POINT(80.28 13.05)"
    assert shelter.status == "open"


def test_update_shelter_only_touches_provided_fields():
    shelter = SimpleNamespace(name="Old", lat=1.0, lon=2.0, status="open", capacity=None, address=None,
                              geom="SRID=4326;POINT(2.0 1.0)")
    db = _ShelterDb()
    service.update_shelter(db, shelter, status="closed")
    assert shelter.status == "closed"
    assert shelter.name == "Old"  # untouched
    assert db.committed


def test_update_shelter_refreshes_geom_when_location_changes():
    shelter = SimpleNamespace(name="Old", lat=1.0, lon=2.0, status="open", capacity=None, address=None,
                              geom="SRID=4326;POINT(2.0 1.0)")
    db = _ShelterDb()
    service.update_shelter(db, shelter, lat=5.0, lon=6.0)
    assert shelter.geom == "SRID=4326;POINT(6.0 5.0)"


def test_delete_shelter_deletes_and_commits():
    shelter = SimpleNamespace()
    db = _ShelterDb()
    service.delete_shelter(db, shelter)
    assert db.deleted == [shelter]
    assert db.committed


# --- nearest_open_shelter -----------------------------------------------------

def test_nearest_open_shelter_picks_closest():
    near = SimpleNamespace(name="Near", lat=13.06, lon=80.30, status="open")
    far = SimpleNamespace(name="Far", lat=37.755, lon=-122.839, status="open")
    db = _ShelterDb([far, near])
    result = service.nearest_open_shelter(db, 13.05, 80.2824)
    assert result.name == "Near"


def test_nearest_open_shelter_returns_none_without_any_open_shelter():
    assert service.nearest_open_shelter(_ShelterDb([]), 13.05, 80.28) is None


# --- exclude_polygons ----------------------------------------------------------

class _ExcludeDb:
    """Two distinct `db.scalars(select(...))` calls happen in exclude_polygons
    (incidents, then alerts) — sequenced by call order, same convention as
    every other multi-query fake db in this test suite."""

    def __init__(self, incidents=None, alerts=None):
        self._incidents = incidents or []
        self._alerts = alerts or []
        self._calls = 0

    def scalars(self, stmt):
        self._calls += 1
        return _FakeScalars(self._incidents if self._calls == 1 else self._alerts)


def test_exclude_polygons_includes_corroborated_incident_cells(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(routing_active_incident_hours=24.0))
    incident = SimpleNamespace(h3_cells=[_CELL_A, _CELL_B])
    db = _ExcludeDb(incidents=[incident], alerts=[])
    polygons = service.exclude_polygons(db)
    assert len(polygons) == 2  # one ring per unique cell


def test_exclude_polygons_includes_warning_alert_cells(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(routing_active_incident_hours=24.0))
    alert = SimpleNamespace(h3_cells=[_CELL_C])
    db = _ExcludeDb(incidents=[], alerts=[alert])
    polygons = service.exclude_polygons(db)
    assert len(polygons) == 1


def test_exclude_polygons_dedupes_overlapping_cells(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(routing_active_incident_hours=24.0))
    incident = SimpleNamespace(h3_cells=[_CELL_A])
    alert = SimpleNamespace(h3_cells=[_CELL_A])  # same cell as the incident
    db = _ExcludeDb(incidents=[incident], alerts=[alert])
    polygons = service.exclude_polygons(db)
    assert len(polygons) == 1


def test_exclude_polygons_empty_when_nothing_active(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(routing_active_incident_hours=24.0))
    assert service.exclude_polygons(_ExcludeDb()) == []


# --- route_to_safety -----------------------------------------------------------

def test_route_to_safety_returns_none_shelter_without_any_open_shelter(monkeypatch):
    monkeypatch.setattr(service, "nearest_open_shelter", lambda db, lat, lon: None)
    result = service.route_to_safety(object(), 13.05, 80.28)
    assert result == {"shelter": None, "route": None, "excluded_cells": 0, "avoided_hazards": False}


def test_route_to_safety_composes_shelter_and_valhalla_route(monkeypatch):
    shelter = SimpleNamespace(
        id="s1", name="Test Shelter", lat=13.10, lon=80.29, capacity=200, status="open", address="Somewhere",
    )
    monkeypatch.setattr(service, "nearest_open_shelter", lambda db, lat, lon: shelter)
    monkeypatch.setattr(service, "exclude_polygons", lambda db: [[[80.0, 13.0], [80.1, 13.0], [80.1, 13.1], [80.0, 13.0]]])
    monkeypatch.setattr(
        service, "get_settings", lambda: SimpleNamespace(routing_default_costing="pedestrian")
    )

    captured = {}

    def fake_route(locations, costing, exclude_polygons):
        captured["locations"] = locations
        captured["costing"] = costing
        captured["exclude_polygons"] = exclude_polygons
        return {
            "trip": {
                "legs": [{"shape": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"}],
                "summary": {"length": 5.4321, "time": 1234.5},
            }
        }

    monkeypatch.setattr(service.client, "route", fake_route)

    result = service.route_to_safety(object(), 13.05, 80.2824)

    assert captured["locations"] == [{"lat": 13.05, "lon": 80.2824}, {"lat": 13.10, "lon": 80.29}]
    assert captured["costing"] == "pedestrian"
    assert captured["exclude_polygons"]  # non-empty polygons were passed through

    assert result["shelter"]["name"] == "Test Shelter"
    assert result["route"]["type"] == "Feature"
    assert result["route"]["geometry"]["type"] == "LineString"
    assert len(result["route"]["geometry"]["coordinates"]) > 0
    assert result["route"]["properties"]["distance_km"] == 5.43
    assert result["route"]["properties"]["duration_min"] == 20.6
    assert result["excluded_cells"] == 1
    assert result["avoided_hazards"] is True


def test_route_to_safety_propagates_routing_unavailable_without_any_hazards_to_exclude(monkeypatch):
    shelter = SimpleNamespace(id="s1", name="X", lat=13.1, lon=80.3, capacity=None, status="open", address=None)
    monkeypatch.setattr(service, "nearest_open_shelter", lambda db, lat, lon: shelter)
    monkeypatch.setattr(service, "exclude_polygons", lambda db: [])
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(routing_default_costing="pedestrian"))

    def raise_unavailable(locations, costing, exclude_polygons):
        raise RoutingUnavailable("Valhalla unreachable: connection refused")

    monkeypatch.setattr(service.client, "route", raise_unavailable)

    try:
        service.route_to_safety(object(), 13.05, 80.28)
        assert False, "expected RoutingUnavailable to propagate"
    except RoutingUnavailable:
        pass


def test_route_to_safety_falls_back_without_exclusion_when_hazard_zone_traps_the_route(monkeypatch):
    """A hard exclusion can leave no reachable edge at all when the traveler's
    own starting point sits inside the excluded hazard geometry — a route
    that isn't guaranteed to avoid the hazard is still better than none."""
    shelter = SimpleNamespace(id="s1", name="X", lat=13.1, lon=80.3, capacity=None, status="open", address=None)
    monkeypatch.setattr(service, "nearest_open_shelter", lambda db, lat, lon: shelter)
    monkeypatch.setattr(service, "exclude_polygons", lambda db: [[[80.0, 13.0], [80.1, 13.0], [80.1, 13.1]]])
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(routing_default_costing="pedestrian"))

    calls = []

    def fake_route(locations, costing, exclude_polygons):
        calls.append(exclude_polygons)
        if exclude_polygons:
            raise RoutingUnavailable("Valhalla returned 400: no path could be found for input")
        return {"trip": {"legs": [{"shape": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"}], "summary": {"length": 3.0, "time": 600.0}}}

    monkeypatch.setattr(service.client, "route", fake_route)

    result = service.route_to_safety(object(), 13.05, 80.28)

    assert len(calls) == 2
    assert calls[0]  # first attempt included the exclusion
    assert calls[1] is None  # retry dropped it
    assert result["route"] is not None
    assert result["avoided_hazards"] is False
    assert result["excluded_cells"] == 1  # still reports how much hazard geometry existed


def test_route_to_safety_propagates_when_even_fallback_without_exclusion_fails(monkeypatch):
    shelter = SimpleNamespace(id="s1", name="X", lat=13.1, lon=80.3, capacity=None, status="open", address=None)
    monkeypatch.setattr(service, "nearest_open_shelter", lambda db, lat, lon: shelter)
    monkeypatch.setattr(service, "exclude_polygons", lambda db: [[[80.0, 13.0], [80.1, 13.0], [80.1, 13.1]]])
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(routing_default_costing="pedestrian"))

    def raise_unavailable(locations, costing, exclude_polygons):
        raise RoutingUnavailable("no path at all, excluded or not")

    monkeypatch.setattr(service.client, "route", raise_unavailable)

    try:
        service.route_to_safety(object(), 13.05, 80.28)
        assert False, "expected RoutingUnavailable to propagate"
    except RoutingUnavailable:
        pass
