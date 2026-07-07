"""Station sync, ERDDAP polling, and anomaly upkeep (run by the scheduler)."""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import SensorReading, Station, StationAnomaly
from app.modules.scoring.audit import append_audit
from app.modules.sensors import anomaly as anomaly_mod
from app.modules.sensors.erddap import fetch_readings

log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "stations.json"
BASELINE_DAYS = 7


def load_station_configs() -> list[dict]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return [c for c in json.load(f) if c.get("enabled")]


def sync_stations(db: Session) -> None:
    """Upsert configured stations into the DB (called at startup)."""
    for cfg in load_station_configs():
        station = db.get(Station, cfg["id"])
        if station is None:
            station = Station(id=cfg["id"])
            db.add(station)
        station.name = cfg["name"]
        station.provider = cfg["provider"]
        station.lat = cfg["lat"]
        station.lon = cfg["lon"]
        station.geom = f"SRID=4326;POINT({cfg['lon']} {cfg['lat']})"
        station.variables = [v["label"] for v in cfg["variables"]]
    db.commit()


def insert_readings(db: Session, station_id: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(SensorReading).values(
        [
            {"time": r["time"], "station_id": station_id, "variable": r["variable"], "value": r["value"]}
            for r in rows
        ]
    ).on_conflict_do_nothing()
    result = db.execute(stmt)
    # executemany drivers may report -1; treat unknown as "all offered rows".
    return result.rowcount if (result.rowcount or -1) >= 0 else len(rows)


def poll_all(db: Session) -> None:
    now = datetime.now(timezone.utc)
    for cfg in load_station_configs():
        last = db.scalar(
            select(func.max(SensorReading.time)).where(SensorReading.station_id == cfg["id"])
        )
        since = max(last, now - timedelta(hours=cfg.get("lookback_hours", 12))) if last else (
            now - timedelta(hours=cfg.get("lookback_hours", 12))
        )
        rows = fetch_readings(cfg, since)
        inserted = insert_readings(db, cfg["id"], rows)
        station = db.get(Station, cfg["id"])
        if station is not None:
            station.last_polled_at = now
        db.commit()
        if inserted:
            log.info("Station %s: %d new readings", cfg["id"], inserted)


def detect_anomalies(db: Session) -> None:
    """z-score latest reading per station/variable vs. trailing baseline; keep
    the active-anomaly set current. Works for real and drill-injected data."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    baseline_start = now - timedelta(days=BASELINE_DAYS)

    pairs = db.execute(
        select(SensorReading.station_id, SensorReading.variable)
        .where(SensorReading.time >= baseline_start)
        .distinct()
    ).all()

    for station_id, variable in pairs:
        rows = db.execute(
            select(SensorReading.time, SensorReading.value)
            .where(SensorReading.station_id == station_id)
            .where(SensorReading.variable == variable)
            .where(SensorReading.time >= baseline_start)
            .order_by(SensorReading.time.asc())
        ).all()
        if not rows:
            continue
        latest_time, latest_value = rows[-1]
        # Stale data can't corroborate a live report.
        if latest_time < now - timedelta(hours=settings.anomaly_active_hours):
            continue
        baseline = [v for _, v in rows[:-1]]
        result = anomaly_mod.detect(baseline, latest_value)

        active = db.scalars(
            select(StationAnomaly)
            .where(StationAnomaly.station_id == station_id)
            .where(StationAnomaly.variable == variable)
            .where(StationAnomaly.active.is_(True))
        ).all()

        if result is not None and abs(result.zscore) >= settings.anomaly_zscore_threshold:
            if active:
                for a in active:  # refresh existing
                    a.zscore = result.zscore
                    a.value = result.value
                    a.detected_at = now
            else:
                anom = StationAnomaly(
                    station_id=station_id,
                    variable=variable,
                    zscore=result.zscore,
                    value=result.value,
                    baseline_mean=result.baseline_mean,
                    baseline_std=result.baseline_std,
                    detected_at=now,
                )
                db.add(anom)
                db.flush()
                append_audit(
                    db,
                    event_type="anomaly.detected",
                    subject_type="station",
                    subject_id=station_id,
                    payload={
                        "variable": variable,
                        "zscore": round(result.zscore, 3),
                        "value": result.value,
                    },
                )
        else:
            for a in active:
                a.active = False
                append_audit(
                    db,
                    event_type="anomaly.resolved",
                    subject_type="station",
                    subject_id=station_id,
                    payload={"variable": variable},
                )

    # Expire anomalies that stopped refreshing (e.g. station went silent).
    cutoff = now - timedelta(hours=settings.anomaly_active_hours)
    stale = db.scalars(
        select(StationAnomaly)
        .where(StationAnomaly.active.is_(True))
        .where(StationAnomaly.detected_at < cutoff)
    ).all()
    for a in stale:
        a.active = False
    db.commit()
