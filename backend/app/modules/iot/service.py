"""Turn a parsed telemetry message into rows in the shared sensor tables.

The whole point of the IoT pilot is that a node is *not* a new kind of thing:
it's a Station with provider "iot", and its readings go into the same
sensor_readings hypertable that ERDDAP writes to, so the existing anomaly
detection and confidence-scoring paths pick them up with no changes. This
module is the small amount of glue that makes that true; there is deliberately
no IoT-specific anomaly or scoring code.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Station
from app.modules.iot.parser import Telemetry, IotMessageError
from app.modules.scoring.audit import append_audit
from app.modules.sensors.service import insert_readings

log = logging.getLogger(__name__)

IOT_PROVIDER = "iot"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _upsert_station(db: Session, tel: Telemetry, now: datetime) -> bool:
    """Create the station on first contact, or refresh its liveness (and
    position, for a drifting node) on later messages. Returns True if this was
    a first registration.

    A brand-new node must carry a location — we can't place a station we've
    never heard of on the map without one. A node we already know can omit it
    and just stream readings."""
    station = db.get(Station, tel.station_id)
    first_seen = station is None
    if first_seen:
        if tel.lat is None or tel.lon is None:
            raise IotMessageError(
                f"first telemetry from {tel.node_id!r} has no lat/lon — can't register a node without a location"
            )
        station = Station(
            id=tel.station_id,
            name=tel.name or f"IoT node {tel.node_id}",
            provider=IOT_PROVIDER,
            lat=tel.lat,
            lon=tel.lon,
            geom=f"SRID=4326;POINT({tel.lon} {tel.lat})",
            variables=[],
        )
        db.add(station)
    else:
        if tel.name:
            station.name = tel.name
        if tel.lat is not None and tel.lon is not None:
            station.lat = tel.lat
            station.lon = tel.lon
            station.geom = f"SRID=4326;POINT({tel.lon} {tel.lat})"

    # Keep the station's variable list a superset of everything it's reported,
    # so /map/stations and the forecast job know what this node measures.
    known = set(station.variables or [])
    known.update(r.variable for r in tel.readings)
    station.variables = sorted(known)
    station.last_polled_at = now
    return first_seen


def ingest_telemetry(db: Session, tel: Telemetry, *, now: datetime | None = None) -> dict:
    """Persist one node's telemetry: upsert the station, insert its readings,
    and audit-log a first registration. Commits. Returns a small summary the
    bridge logs."""
    now = now or _utcnow()
    first_seen = _upsert_station(db, tel, now)
    db.flush()

    inserted = insert_readings(
        db,
        tel.station_id,
        [{"time": r.time, "variable": r.variable, "value": r.value} for r in tel.readings],
    )

    if first_seen:
        append_audit(
            db,
            event_type="iot.node_registered",
            subject_type="station",
            subject_id=tel.station_id,
            payload={"node_id": tel.node_id, "lat": tel.lat, "lon": tel.lon,
                     "variables": sorted({r.variable for r in tel.readings})},
        )

    db.commit()
    log.info(
        "IoT node %s: %s, %d reading(s) inserted",
        tel.node_id, "registered" if first_seen else "updated", inserted,
    )
    return {"station_id": tel.station_id, "first_seen": first_seen, "inserted": inserted}
