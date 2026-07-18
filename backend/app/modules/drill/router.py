"""Drill mode: analyst-guarded endpoints for injecting synthetic sensor data
and forcing pipeline ticks, so full disaster drills run without a disaster."""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.redisclient import get_redis
from app.core.security import require_analyst
from app.models import Station
from app.modules.geo.hotspots import CACHE_KEY as HOTSPOT_CACHE_KEY
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


@router.post("/tick")
def tick(_: str = Depends(require_analyst), db: Session = Depends(get_db)) -> dict:
    """Run anomaly detection + satellite polling + rescoring immediately
    instead of waiting for the scheduler — keeps drills and demos snappy."""
    detect_anomalies(db)
    satellite_observed = poll_satellite(db)
    rescored = rescore_recent(db)
    try:  # drop the cached hotspot layer so the drill sees fresh clusters
        get_redis().delete(HOTSPOT_CACHE_KEY)
    except Exception:
        pass
    return {"rescored_reports": rescored, "satellite_observations": satellite_observed}
