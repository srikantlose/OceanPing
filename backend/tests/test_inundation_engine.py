from app.modules.inundation import engine


def test_flooded_cells_includes_cells_at_or_below_level():
    elevations = {"a": 1.0, "b": 2.0, "c": 3.0}
    flooded = engine.flooded_cells(elevations, 2.0)
    assert set(flooded) == {"a", "b"}


def test_flooded_cells_depth_is_level_minus_elevation():
    flooded = engine.flooded_cells({"a": 0.5}, 2.0)
    assert flooded["a"] == 1.5


def test_flooded_cells_empty_below_lowest_elevation():
    assert engine.flooded_cells({"a": 5.0, "b": 6.0}, 1.0) == {}


def test_flooded_cells_nests_monotonically_with_rising_level():
    """Known DEM fixture — raising the level can only add cells, never
    remove one (the phase-3 plan's verification requirement)."""
    elevations = {"a": -1.0, "b": 0.5, "c": 1.5, "d": 3.0, "e": 10.0}
    low = set(engine.flooded_cells(elevations, 1.0))
    high = set(engine.flooded_cells(elevations, 2.0))
    assert low < high  # strict subset
    assert low == {"a", "b"}
    assert high == {"a", "b", "c"}


def test_flooded_cells_handles_empty_elevation_table():
    assert engine.flooded_cells({}, 5.0) == {}
