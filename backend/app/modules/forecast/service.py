"""DB-facing forecast generation, wiring, and validation.

Both forecast kinds are data-gated-degrade, same as every other real
integration in this app: sensor forecasts need enough history at a
station/variable to fit against (engine.MIN_SENSOR_POINTS); propagation
forecasts need enough time-ordered reports in an incident to fit a moving
front (engine.MIN_FRONT_POINTS), and only actually project anything once the
fitted front clears a noise floor. Either path returns None rather than
fabricate a forecast from insufficient data.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Forecast, Incident, Report, SensorReading, Station
from app.modules.forecast import engine
from app.modules.geo.distance import haversine_km
from app.modules.ivr.locations import PILOT_LOCATIONS
from app.modules.scoring.audit import append_audit
from app.modules.sensors.service import load_station_configs

log = logging.getLogger(__name__)

# The plan's own "project 1-3h ahead" framing — a specific product decision,
# not a tunable, same as sitrep/service.py's HOTSPOT_MATCH_KM.
PROPAGATION_HORIZONS_HOURS = (1.0, 2.0, 3.0)
VALIDATION_TIME_TOLERANCE_MINUTES = 20  # matching an actual reading to a predicted timestamp


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def nearest_pilot_location(lat: float, lon: float) -> str:
    """Bucket a lat/lon into the nearest named pilot coastal location. This
    app's data model has no administrative-district field, so the public
    accuracy endpoint groups by landmark instead — the same honest stand-in
    modules/ivr/locations.py already uses for "which district is this
    caller in"."""
    return min(PILOT_LOCATIONS, key=lambda loc: haversine_km(lat, lon, loc["lat"], loc["lon"]))["name"]


# ---------------------------------------------------------------------------
# Sensor forecasts
# ---------------------------------------------------------------------------


def generate_sensor_forecast(
    db: Session, station_id: str, variable: str, *, as_of: datetime | None = None
) -> Forecast | None:
    """Fit a harmonic-trend forecast for one station/variable from its
    trailing history. `as_of` lets a drill backtest against already-recorded
    history — fit only on readings at or before `as_of`, so the forecasted
    window lands on real readings already sitting in the DB and can be
    validated immediately instead of waiting on real wall-clock time. The
    scheduled path always calls this with the default (now)."""
    settings = get_settings()
    reference = as_of or _utcnow()
    since = reference - timedelta(days=settings.forecast_sensor_baseline_days)
    rows = db.execute(
        select(SensorReading.time, SensorReading.value)
        .where(SensorReading.station_id == station_id)
        .where(SensorReading.variable == variable)
        .where(SensorReading.time >= since)
        .where(SensorReading.time <= reference)
        .order_by(SensorReading.time.asc())
    ).all()
    times = [r[0] for r in rows]
    values = [r[1] for r in rows]
    points = engine.fit_sensor_forecast(
        times, values,
        horizon_hours=settings.forecast_sensor_horizon_hours,
        step_minutes=settings.forecast_sensor_step_minutes,
    )
    if points is None:
        return None

    forecast = Forecast(
        kind="sensor",
        subject_type="station",
        subject_id=station_id,
        generated_at=reference,
        horizon_hours=settings.forecast_sensor_horizon_hours,
        content={"variable": variable, "points": points, "model": "harmonic_trend_v1"},
    )
    db.add(forecast)
    db.flush()
    append_audit(
        db, event_type="forecast.generated", subject_type="station", subject_id=station_id,
        payload={"kind": "sensor", "variable": variable, "forecast_id": str(forecast.id)},
    )
    return forecast


def generate_sensor_forecasts(db: Session) -> int:
    """One forecast per enabled station/variable pair with enough history —
    the scheduler's normal (non-backtest) path."""
    n = 0
    for cfg in load_station_configs():
        for var in cfg["variables"]:
            if generate_sensor_forecast(db, cfg["id"], var["label"]) is not None:
                n += 1
    db.commit()
    return n


def latest_sensor_forecast_point(db: Session, variable: str, hours_ahead: float) -> dict | None:
    """From the freshest sensor forecast for this variable (any station
    carrying it — same "any station" convention inundation/service.py's live
    lookup already uses), the predicted point nearest to `hours_ahead` from
    that forecast's generation time. None if no such forecast exists yet."""
    forecasts = db.scalars(
        select(Forecast).where(Forecast.kind == "sensor").order_by(Forecast.generated_at.desc()).limit(50)
    ).all()
    forecast = next((f for f in forecasts if f.content.get("variable") == variable), None)
    if forecast is None:
        return None
    points = forecast.content.get("points") or []
    if not points:
        return None
    target = forecast.generated_at + timedelta(hours=hours_ahead)
    return min(points, key=lambda p: abs((datetime.fromisoformat(p["time"]) - target).total_seconds()))


def station_forecast_series(db: Session, station_id: str) -> dict[str, list]:
    """variable -> [[iso_time, value], ...] from that station's freshest
    sensor forecast per variable — feeds the map's dashed forecast extension
    on a station's sparkline. Empty for a variable with no forecast yet."""
    forecasts = db.scalars(
        select(Forecast)
        .where(Forecast.kind == "sensor")
        .where(Forecast.subject_id == station_id)
        .order_by(Forecast.generated_at.desc())
        .limit(20)
    ).all()
    series: dict[str, list] = {}
    for f in forecasts:
        variable = f.content.get("variable")
        if variable is None or variable in series:
            continue
        series[variable] = [[p["time"], p["value"]] for p in (f.content.get("points") or [])]
    return series


# ---------------------------------------------------------------------------
# Propagation forecasts
# ---------------------------------------------------------------------------


def _incident_report_sequence(incident: Incident) -> list[tuple[datetime, float, float]]:
    return [(r.created_at, r.lat, r.lon) for r in sorted(incident.reports, key=lambda r: r.created_at)]


def generate_propagation_forecast(db: Session, incident: Incident) -> Forecast | None:
    """Fit a moving front from an incident's own report history and project
    it 1-3h ahead. None if the incident's reports don't carry enough
    directional signal to fit a front (most tightly-jittered clusters won't)."""
    front = engine.fit_front(_incident_report_sequence(incident))
    if front is None:
        return None

    current_cells = set(incident.h3_cells or [])
    projected: dict[str, list[str]] = {}
    for h in PROPAGATION_HORIZONS_HOURS:
        ahead = engine.project_front_cells(front, current_cells, h) - current_cells
        projected[str(h)] = sorted(ahead)

    content = {
        "hazard_type": incident.hazard_type,
        "location": nearest_pilot_location(incident.centroid_lat, incident.centroid_lon),
        "front": {"speed_kmh": round(front.speed_kmh, 2), "bearing_deg": round(front.bearing_deg, 1)},
        "origin_cells": sorted(current_cells),
        "projected": projected,
    }
    forecast = Forecast(
        kind="propagation",
        subject_type="incident",
        subject_id=str(incident.id),
        hazard_type=incident.hazard_type,
        generated_at=_utcnow(),
        horizon_hours=max(PROPAGATION_HORIZONS_HOURS),
        content=content,
    )
    db.add(forecast)
    db.flush()
    append_audit(
        db, event_type="forecast.generated", subject_type="incident", subject_id=str(incident.id),
        payload={"kind": "propagation", "speed_kmh": content["front"]["speed_kmh"], "forecast_id": str(forecast.id)},
    )
    return forecast


def generate_propagation_forecasts(db: Session) -> int:
    """One forecast per incident still active in the propagation window,
    with enough directional report history to fit a front."""
    settings = get_settings()
    since = _utcnow() - timedelta(hours=settings.forecast_propagation_incident_hours)
    incidents = db.scalars(
        select(Incident).where(Incident.last_seen >= since).where(Incident.status != "rejected")
    ).all()
    n = 0
    for inc in incidents:
        if generate_propagation_forecast(db, inc) is not None:
            n += 1
    db.commit()
    return n


def latest_projected_cells(db: Session, incident_id, *, within_hours: float = 1.0) -> list[str]:
    """Nearest-horizon projected cells from the freshest propagation forecast
    for this incident, if one exists and isn't stale — [] otherwise (no
    forecast yet, or the front wasn't fittable). Called from
    alerts/service.py to snapshot onto an alert at issue/upgrade time."""
    cutoff = _utcnow() - timedelta(hours=within_hours * 2)  # generous vs. the generation cadence
    forecast = db.scalars(
        select(Forecast)
        .where(Forecast.kind == "propagation")
        .where(Forecast.subject_id == str(incident_id))
        .where(Forecast.generated_at >= cutoff)
        .order_by(Forecast.generated_at.desc())
        .limit(1)
    ).first()
    if forecast is None:
        return []
    nearest_horizon = min(PROPAGATION_HORIZONS_HOURS)
    return list(forecast.content.get("projected", {}).get(str(nearest_horizon), []))


# ---------------------------------------------------------------------------
# Validation loop
# ---------------------------------------------------------------------------


def _validate_sensor_forecast(db: Session, forecast: Forecast) -> dict | None:
    points = forecast.content.get("points") or []
    variable = forecast.content.get("variable")
    if not points:
        return None
    last_time = datetime.fromisoformat(points[-1]["time"])
    if last_time > _utcnow():
        return None  # horizon hasn't fully elapsed yet

    tol = timedelta(minutes=VALIDATION_TIME_TOLERANCE_MINUTES)
    errors = []
    for p in points:
        target = datetime.fromisoformat(p["time"])
        rows = db.execute(
            select(SensorReading.time, SensorReading.value)
            .where(SensorReading.station_id == forecast.subject_id)
            .where(SensorReading.variable == variable)
            .where(SensorReading.time >= target - tol)
            .where(SensorReading.time <= target + tol)
        ).all()
        if rows:
            nearest = min(rows, key=lambda r: abs((r[0] - target).total_seconds()))
            errors.append(abs(nearest[1] - p["value"]))

    if not errors:
        return {"scored_points": 0, "mean_abs_error": None}
    return {"scored_points": len(errors), "mean_abs_error": round(sum(errors) / len(errors), 4)}


def _validate_propagation_forecast(db: Session, forecast: Forecast) -> dict | None:
    projected = forecast.content.get("projected") or {}
    horizons = sorted((float(h) for h in projected), reverse=True)
    if not horizons:
        return None
    if forecast.generated_at + timedelta(hours=horizons[0]) > _utcnow():
        return None  # furthest horizon hasn't fully elapsed yet

    per_horizon = {}
    for h in horizons:
        cells = set(projected[str(h)])
        if not cells:
            per_horizon[str(h)] = {"cells": 0, "hit_cells": 0}
            continue
        horizon_end = forecast.generated_at + timedelta(hours=h)
        hit_cells = {
            r.h3_cell
            for r in db.scalars(
                select(Report)
                .where(Report.h3_cell.in_(cells))
                .where(Report.created_at >= forecast.generated_at)
                .where(Report.created_at <= horizon_end)
            ).all()
        }
        per_horizon[str(h)] = {"cells": len(cells), "hit_cells": len(hit_cells)}

    total_cells = sum(v["cells"] for v in per_horizon.values())
    total_hits = sum(v["hit_cells"] for v in per_horizon.values())
    return {
        "per_horizon": per_horizon,
        "hit_rate": round(total_hits / total_cells, 4) if total_cells else None,
    }


def validate_forecasts(db: Session) -> int:
    """Score every unvalidated forecast whose full horizon has already
    elapsed: a sensor forecast against actual readings at its predicted
    times, a propagation forecast against whether reports actually appeared
    in its projected cells. Leaves anything whose horizon hasn't passed yet
    (or with nothing to score) unvalidated for a later pass."""
    pending = db.scalars(select(Forecast).where(Forecast.validated_at.is_(None))).all()
    n = 0
    for forecast in pending:
        if forecast.kind == "sensor":
            result = _validate_sensor_forecast(db, forecast)
        else:
            result = _validate_propagation_forecast(db, forecast)
        if result is None:
            continue
        forecast.validation = result
        forecast.validated_at = _utcnow()
        n += 1
    db.commit()
    return n


def accuracy_summary(db: Session) -> dict:
    """Public 'how right were we' rollup, bucketed by nearest named pilot
    location (see nearest_pilot_location for why — this app has no
    administrative-district field)."""
    scored = db.scalars(select(Forecast).where(Forecast.validated_at.is_not(None))).all()
    sensor_by_key: dict[tuple[str, str], list[float]] = {}
    prop_by_key: dict[tuple[str, str], list[float]] = {}

    for f in scored:
        if f.kind == "sensor":
            mae = (f.validation or {}).get("mean_abs_error")
            if mae is None:
                continue
            station = db.get(Station, f.subject_id)
            loc = nearest_pilot_location(station.lat, station.lon) if station else "unknown"
            sensor_by_key.setdefault((loc, f.content.get("variable")), []).append(mae)
        else:
            hit_rate = (f.validation or {}).get("hit_rate")
            if hit_rate is None:
                continue
            loc = f.content.get("location", "unknown")
            prop_by_key.setdefault((loc, f.hazard_type), []).append(hit_rate)

    return {
        "sensor": [
            {
                "location": loc, "variable": var, "n_forecasts": len(vals),
                "mean_abs_error": round(sum(vals) / len(vals), 4),
            }
            for (loc, var), vals in sorted(sensor_by_key.items())
        ],
        "propagation": [
            {
                "location": loc, "hazard_type": hz, "n_forecasts": len(vals),
                "mean_hit_rate": round(sum(vals) / len(vals), 4),
            }
            for (loc, hz), vals in sorted(prop_by_key.items())
        ],
    }
