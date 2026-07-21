"""Drill mode: analyst-guarded endpoints for injecting synthetic sensor data
and forcing pipeline ticks, so full disaster drills run without a disaster."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.redisclient import get_redis
from app.core.security import require_analyst
from app.models import Station
from app.modules.fisherman.service import refresh_pfz_advisories
from app.modules.forecast.service import (
    generate_propagation_forecasts,
    generate_sensor_forecast,
    generate_sensor_forecasts,
    validate_forecasts,
)
from app.modules.geo.hotspots import CACHE_KEY as HOTSPOT_CACHE_KEY
from app.modules.ingest.service import RateLimited, create_report
from app.modules.narratives.service import detect_narratives
from app.modules.satellite.service import poll_satellite
from app.modules.scoring.service import rescore_recent
from app.modules.sensors.service import detect_anomalies, insert_readings

router = APIRouter(prefix="/drill", tags=["drill"])


class InjectReadingsIn(BaseModel):
    station_id: str
    name: str = "Drill station"
    lat: float
    lon: float
    variable: str = "water_level"
    points: list[tuple[datetime, float]]


@router.post("/inject-readings")
def inject_readings(
    body: InjectReadingsIn,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    station = db.get(Station, body.station_id)
    if station is None:
        station = Station(
            id=body.station_id,
            name=body.name,
            provider="drill",
            lat=body.lat,
            lon=body.lon,
            geom=f"SRID=4326;POINT({body.lon} {body.lat})",
            variables=[body.variable],
        )
        db.add(station)
        db.flush()
    inserted = insert_readings(
        db,
        body.station_id,
        [{"time": t, "variable": body.variable, "value": v} for t, v in body.points],
    )
    db.commit()
    return {"station_id": body.station_id, "inserted": inserted}


class InjectReportIn(BaseModel):
    lat: float
    lon: float
    hazard_type: str
    client_id: str
    text: str | None = None
    created_at: datetime


class InjectReportsIn(BaseModel):
    reports: list[InjectReportIn]


@router.post("/inject-reports")
def inject_reports(
    body: InjectReportsIn,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    """Submit reports with an explicit `created_at` — the public /reports
    endpoint always stamps "now", so a drill needing a real time-ordered,
    directionally-moving report sequence (to exercise hazard-front
    propagation fitting) goes through here instead."""
    created = []
    for r in body.reports:
        try:
            report = create_report(
                db, source="drill", external_id=r.client_id, lat=r.lat, lon=r.lon,
                hazard_type=r.hazard_type, text=r.text, created_at=r.created_at,
            )
        except RateLimited as exc:
            raise HTTPException(status_code=429, detail=str(exc))
        created.append(str(report.id))
    return {"created": created}


class BacktestForecastIn(BaseModel):
    station_id: str
    variable: str
    hours_ago: float


@router.post("/backtest-forecast")
def backtest_forecast(
    body: BacktestForecastIn,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    """Fit a sensor forecast as of `hours_ago` in the past instead of now, so
    its predicted window lands on real readings already in the DB — lets a
    drill exercise the full generate-then-validate loop immediately instead
    of waiting hours of real wall-clock time for a forecast's horizon to
    elapse."""
    as_of = datetime.now(timezone.utc) - timedelta(hours=body.hours_ago)
    forecast = generate_sensor_forecast(db, body.station_id, body.variable, as_of=as_of)
    db.commit()
    if forecast is None:
        raise HTTPException(status_code=404, detail="Not enough history before that time to backtest a forecast")
    return {
        "id": str(forecast.id),
        "generated_at": forecast.generated_at.isoformat(),
        "content": forecast.content,
    }


@router.post("/tick")
def tick(_: str = Depends(require_analyst), db: Session = Depends(get_db)) -> dict:
    """Run anomaly detection + satellite polling + rescoring immediately
    instead of waiting for the scheduler — keeps drills and demos snappy."""
    detect_anomalies(db)
    satellite_observed = poll_satellite(db)
    rescored = rescore_recent(db)
    pfz_zones = refresh_pfz_advisories(db)
    sensor_forecasts = generate_sensor_forecasts(db)
    propagation_forecasts = generate_propagation_forecasts(db)
    validated_forecasts = validate_forecasts(db)
    narratives_flagged = detect_narratives(db)
    try:  # drop the cached hotspot layer so the drill sees fresh clusters
        get_redis().delete(HOTSPOT_CACHE_KEY)
    except Exception:
        pass
    return {
        "rescored_reports": rescored,
        "satellite_observations": satellite_observed,
        "sensor_forecasts": sensor_forecasts,
        "propagation_forecasts": propagation_forecasts,
        "validated_forecasts": validated_forecasts,
        "pfz_zones": pfz_zones,
        "narratives_flagged": narratives_flagged,
    }
