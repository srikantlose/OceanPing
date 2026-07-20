from types import SimpleNamespace

from app.modules.geo.h3utils import cell_for
from app.modules.inundation import service

_CELL_LOW = cell_for(13.05, 80.28)
_CELL_HIGH = cell_for(13.10, 80.30)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Db:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, stmt):
        return _FakeResult(self._rows)


# --- load_elevation_table / flooded_cells_geojson ---------------------------

def test_load_elevation_table_returns_cell_to_elevation_mapping():
    db = _Db([(_CELL_LOW, 1.0), (_CELL_HIGH, 2.5)])
    assert service.load_elevation_table(db) == {_CELL_LOW: 1.0, _CELL_HIGH: 2.5}


def test_flooded_cells_geojson_shape():
    db = _Db([(_CELL_LOW, 1.0)])
    fc = service.flooded_cells_geojson(db, 2.0)
    assert fc["type"] == "FeatureCollection"
    assert fc["cell_count"] == 1
    assert fc["water_level_m"] == 2.0
    assert fc["features"][0]["properties"] == {"h3_cell": _CELL_LOW, "depth_m": 1.0}
    assert fc["features"][0]["geometry"]["type"] == "Polygon"


def test_flooded_cells_geojson_excludes_cells_above_level():
    db = _Db([(_CELL_LOW, 5.0)])
    fc = service.flooded_cells_geojson(db, 2.0)
    assert fc["cell_count"] == 0
    assert fc["features"] == []


# --- latest_water_level -------------------------------------------------------

def test_latest_water_level_returns_value_when_present(monkeypatch):
    monkeypatch.setattr(
        service, "get_settings",
        lambda: SimpleNamespace(inundation_reference_variable="water_level", inundation_wire_hours=2.0),
    )
    assert service.latest_water_level(_Db([(2.6,)])) == 2.6


def test_latest_water_level_returns_none_without_any_reading(monkeypatch):
    monkeypatch.setattr(
        service, "get_settings",
        lambda: SimpleNamespace(inundation_reference_variable="water_level", inundation_wire_hours=2.0),
    )
    assert service.latest_water_level(_Db([])) is None


# --- predicted_flooded_cells ---------------------------------------------------

def test_predicted_flooded_cells_empty_without_fresh_reading(monkeypatch):
    monkeypatch.setattr(service, "latest_water_level", lambda db: None)
    assert service.predicted_flooded_cells(object()) == set()


def test_predicted_flooded_cells_applies_bathtub_model_to_current_level(monkeypatch):
    monkeypatch.setattr(service, "latest_water_level", lambda db: 2.0)
    monkeypatch.setattr(service, "load_elevation_table", lambda db: {_CELL_LOW: 1.0, _CELL_HIGH: 5.0})
    assert service.predicted_flooded_cells(object()) == {_CELL_LOW}
