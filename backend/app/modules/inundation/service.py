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
from app.modules.geo.h3utils import cell_for, cell_polygon
from app.modules.inundation import engine

# elevation_cells is built by scripts/inundation/build_elevation_cells.sh at H3
# resolution 9 (finer than this app's default H3_RESOLUTION=8 used everywhere
# else — reports, incidents, damage assessments — since flood risk varies over
# a smaller footprint than a report's fuzzed public location needs to). Any
# code mapping an arbitrary lat/lon onto this table must ask cell_for() for
# resolution 9 explicitly; its own default won't match a row here.
ELEVATION_H3_RESOLUTION = 9


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


def predicted_depth_at_point(db: Session, lat: float, lon: float, hours_ahead: float = 0.0) -> dict:
    """Point query version of flooded_cells_geojson/forecast_flooded_cells_geojson
    for a single lat/lon — the data source AR mode (phase 4, milestone 6) needs
    to ask "how deep would it get right here" instead of rendering a whole
    cell-set map. hours_ahead=0 (default) uses the live gauge reading, the same
    wiring as predicted_flooded_cells(); hours_ahead>0 uses the sensor forecast,
    the same wiring as forecast_flooded_cells_geojson(). Degrades to
    depth_m=None/flooded=False rather than raising when there's no fresh
    reading, no forecast yet, or the point falls outside the DEM extract — same
    data-gated-degrade posture as the rest of this module, since an AR client
    needs a well-formed "nothing to show yet" response to render a normal
    camera view with no line overlaid, not an error."""
    settings = get_settings()
    cell = cell_for(lat, lon, resolution=ELEVATION_H3_RESOLUTION)
    row = db.execute(select(ElevationCell.elevation_m).where(ElevationCell.h3_cell == cell)).first()
    elevation_m = row[0] if row else None

    forecast_time = None
    if hours_ahead > 0:
        point = latest_sensor_forecast_point(db, settings.inundation_reference_variable, hours_ahead)
        water_level_m = point["value"] if point else None
        forecast_time = point["time"] if point else None
    else:
        water_level_m = latest_water_level(db)

    depth_m = None
    if elevation_m is not None and water_level_m is not None:
        depth_m = engine.flooded_cells({cell: elevation_m}, water_level_m).get(cell)

    return {
        "lat": lat,
        "lon": lon,
        "h3_cell": cell,
        "elevation_m": elevation_m,
        "water_level_m": water_level_m,
        "forecast_time": forecast_time,
        "depth_m": depth_m,
        "flooded": depth_m is not None,
    }
