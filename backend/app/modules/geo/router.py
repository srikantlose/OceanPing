"""Public GeoJSON endpoints for the map. Report locations are fuzzed to their
H3 cell centroid — exact coordinates are analyst-only."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Incident, Report, SensorReading, Station, StationAnomaly
from app.modules.geo.h3utils import cell_centroid, cell_polygon
from app.modules.geo.hotspots import hotspots_geojson

router = APIRouter(prefix="/map", tags=["map"])


@router.get("/reports")
def public_reports(db: Session = Depends(get_db)) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=48)
    reports = db.scalars(
        select(Report)
        .where(Report.status == "verified")
        .where(Report.created_at >= since)
        .order_by(Report.created_at.desc())
        .limit(500)
    ).all()
    features = []
    for rep in reports:
        lat, lon = cell_centroid(rep.h3_cell)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "id": str(rep.id),
                    "hazard_type": rep.hazard_type,
                    "urgency": rep.urgency,
                    "confidence": round(rep.confidence, 3),
                    "created_at": rep.created_at.isoformat(),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


@router.get("/incidents")
def public_incidents(db: Session = Depends(get_db)) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=48)
    incidents = db.scalars(
        select(Incident)
        .where(Incident.status == "verified")
        .where(Incident.last_seen >= since)
        .order_by(Incident.last_seen.desc())
        .limit(200)
    ).all()
    features = []
    for inc in incidents:
        cells = [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [cell_polygon(c)]},
             "properties": {"incident_id": str(inc.id)}}
            for c in (inc.h3_cells or [])
        ]
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [inc.centroid_lon, inc.centroid_lat]},
                "properties": {
                    "id": str(inc.id),
                    "hazard_type": inc.hazard_type,
                    "report_count": inc.report_count,
                    "max_confidence": round(inc.max_confidence, 3),
                    "first_seen": inc.first_seen.isoformat(),
                    "last_seen": inc.last_seen.isoformat(),
                    "cells": cells,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


@router.get("/hotspots")
def hotspots(db: Session = Depends(get_db)) -> dict:
    return hotspots_geojson(db)


@router.get("/stations")
def stations(db: Session = Depends(get_db)) -> dict:
    now = datetime.now(timezone.utc)
    stations = db.scalars(select(Station)).all()
    active_anomalies = db.scalars(
        select(StationAnomaly).where(StationAnomaly.active.is_(True))
    ).all()
    anomalies_by_station: dict[str, list[StationAnomaly]] = {}
    for a in active_anomalies:
        anomalies_by_station.setdefault(a.station_id, []).append(a)

    features = []
    for st in stations:
        readings = db.execute(
            select(SensorReading.time, SensorReading.variable, SensorReading.value)
            .where(SensorReading.station_id == st.id)
            .where(SensorReading.time >= now - timedelta(hours=24))
            .order_by(SensorReading.time.asc())
        ).all()
        series: dict[str, list] = {}
        for t, var, val in readings:
            series.setdefault(var, []).append([t.isoformat(), val])
        latest = {var: pts[-1][1] for var, pts in series.items()}
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [st.lon, st.lat]},
                "properties": {
                    "id": st.id,
                    "name": st.name,
                    "provider": st.provider,
                    "latest": latest,
                    "series": series,
                    "anomalies": [
                        {"variable": a.variable, "zscore": round(a.zscore, 2)}
                        for a in anomalies_by_station.get(st.id, [])
                    ],
                    "last_polled_at": st.last_polled_at.isoformat() if st.last_polled_at else None,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}
