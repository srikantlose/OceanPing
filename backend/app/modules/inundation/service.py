"""DB-facing inundation queries: the elevation table, the bathtub model
applied against it, and the "what's the current best-guess water level"
lookup that drives live alert/routing wiring.

predicted_flooded_cells() only returns cells once a real, fresh
`inundation_reference_variable` reading exists — same credential/data-gated-
degrade pattern as every other real integration in this app (see
core/config.py). There is no live INCOIS tide gauge configured in this
environment (stations.json's incois-chennai-tide is disabled), so this stays
empty until a real gauge is configured or a drill injects one.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ElevationCell, SensorReading
from app.modules.forecast.service import latest_sensor_forecast_point
from app.modules.geo.h3utils import cell_polygon
from app.modules.inundation import engine


def load_elevation_table(db: Session) -> dict[str, float]:
    rows = db.execute(select(ElevationCell.h3_cell, ElevationCell.elevation_m)).all()
    return {cell: elevation for cell, elevation in rows}


def flooded_cells_geojson(db: Session, water_level_m: float) -> dict:
    elevations = load_elevation_table(db)
    flooded = engine.flooded_cells(elevations, water_level_m)
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [cell_polygon(cell)]},
            "properties": {"h3_cell": cell, "depth_m": depth},
        }
        for cell, depth in flooded.items()
    ]
    return {
        "type": "FeatureCollection",
        "features": features,
        "water_level_m": water_level_m,
        "cell_count": len(flooded),
    }


def latest_water_level(db: Session) -> float | None:
    """Most recent fresh reading of the reference water-level variable across
    all stations, or None if nothing fresh enough exists to base a live
    prediction on."""
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.inundation_wire_hours)
    row = db.execute(
        select(SensorReading.value)
        .where(SensorReading.variable == settings.inundation_reference_variable)
        .where(SensorReading.time >= cutoff)
        .order_by(SensorReading.time.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def predicted_flooded_cells(db: Session) -> set[str]:
    """Cell set to drive alert/routing wiring right now — empty if there's no
    fresh gauge reading to base a prediction on."""
    level = latest_water_level(db)
    if level is None:
        return set()
    elevations = load_elevation_table(db)
    return set(engine.flooded_cells(elevations, level))


def forecast_flooded_cells_geojson(db: Session, hours_ahead: float) -> dict | None:
    """Bathtub model applied to a *forecasted* future water level (phase 3,
    milestone 3) instead of the current instantaneous reading — closes the
    milestone-1 gap of only ever using "now." None if there's no sensor
    forecast yet for the reference variable to draw from (see
    modules/forecast/service.py — same data-gated-degrade pattern as
    predicted_flooded_cells above)."""
    settings = get_settings()
    point = latest_sensor_forecast_point(db, settings.inundation_reference_variable, hours_ahead)
    if point is None:
        return None
    elevations = load_elevation_table(db)
    flooded = engine.flooded_cells(elevations, point["value"])
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [cell_polygon(cell)]},
            "properties": {"h3_cell": cell, "depth_m": depth},
        }
        for cell, depth in flooded.items()
    ]
    return {
        "type": "FeatureCollection",
        "features": features,
        "water_level_m": point["value"],
        "forecast_time": point["time"],
        "cell_count": len(flooded),
    }
