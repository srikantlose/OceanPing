"""Fisherman mode: cooperative-verified role with elevated starting trust,
plus "give before ask" surfaces (PFZ zones, nearby sea-state) a fisherman can
check any time, not just while filing a report."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import PfzAdvisory, SensorReading, Station, StationAnomaly
from app.modules.fisherman import pfz as pfz_mod
from app.modules.fisherman import roster
from app.modules.geo.distance import haversine_km
from app.modules.ingest.service import FISHERMAN_START_TRUST, get_or_create_reporter

log = logging.getLogger(__name__)

# Chennai — this deployment's pilot area, same point MapView.tsx centers on
# and ivr/locations.py's pilot locations cluster around. Used as the default
# reference point for "nearest station" when a channel has no location handy.
PILOT_CENTROID = (13.05, 80.2824)


def register_fisherman(db: Session, source: str, external_id: str, phone: str) -> tuple:
    """Looks `phone` up against the cooperative roster (roster.py) and, if
    matched, marks this reporter as a fisherman with elevated starting trust
    (unless they already have verification history, in which case their
    earned trust score is left alone). Returns (Reporter, cooperative_name)
    on success, (None, None) if the phone isn't on any cooperative's roll."""
    member = roster.member_for_phone(phone)
    if member is None:
        return None, None
    reporter = get_or_create_reporter(db, source, external_id, role="fisherman")
    if reporter.role != "fisherman":
        reporter.role = "fisherman"
    if (
        reporter.verified_count == 0
        and reporter.debunked_count == 0
        and reporter.trust_score < FISHERMAN_START_TRUST
    ):
        reporter.trust_score = FISHERMAN_START_TRUST
    db.commit()
    return reporter, member["cooperative"]


def refresh_pfz_advisories(db: Session, sector: str = pfz_mod.PILOT_SECTOR) -> int:
    """Replace this sector's advisory batch with a fresh one. Called by the
    scheduler (see core/scheduler.py) at roughly the cadence real INCOIS PFZ
    bulletins are reissued — see pfz.py for why this is a deterministic stub
    rather than a real feed."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    db.query(PfzAdvisory).filter(PfzAdvisory.sector == sector).delete()
    provider = pfz_mod.get_provider()
    zones = provider.fetch(sector)
    for zone in zones:
        db.add(
            PfzAdvisory(
                sector=sector,
                lat=zone["lat"],
                lon=zone["lon"],
                depth_m=zone["depth_m"],
                distance_km=zone["distance_km"],
                bearing=zone["bearing"],
                valid_from=now,
                valid_until=now + timedelta(hours=settings.pfz_validity_hours),
                source=provider.name,
            )
        )
    db.commit()
    return len(zones)


def active_pfz_advisories(db: Session, sector: str = pfz_mod.PILOT_SECTOR) -> list[dict]:
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(PfzAdvisory)
        .where(PfzAdvisory.sector == sector)
        .where(PfzAdvisory.valid_until >= now)
        .order_by(PfzAdvisory.distance_km.asc())
    ).all()
    return [
        {
            "lat": r.lat,
            "lon": r.lon,
            "depth_m": r.depth_m,
            "distance_km": r.distance_km,
            "bearing": r.bearing,
            "valid_until": r.valid_until.isoformat(),
        }
        for r in rows
    ]


def _nearest_station(db: Session, ref_lat: float, ref_lon: float) -> Station | None:
    stations = db.scalars(select(Station)).all()
    if not stations:
        return None
    return min(stations, key=lambda st: haversine_km(ref_lat, ref_lon, st.lat, st.lon))


def _station_sensor_summary(db: Session, station_id: str) -> dict:
    now = datetime.now(timezone.utc)
    readings = db.execute(
        select(SensorReading.variable, SensorReading.value, SensorReading.time)
        .where(SensorReading.station_id == station_id)
        .where(SensorReading.time >= now - timedelta(hours=24))
        .order_by(SensorReading.time.asc())
    ).all()
    latest: dict[str, dict] = {}
    for variable, value, time in readings:
        latest[variable] = {"value": value, "time": time.isoformat()}
    anomalies = db.scalars(
        select(StationAnomaly)
        .where(StationAnomaly.station_id == station_id)
        .where(StationAnomaly.active.is_(True))
    ).all()
    return {
        "latest": latest,
        "anomalies": [{"variable": a.variable, "zscore": round(a.zscore, 2)} for a in anomalies],
    }


def nearest_station_reading(db: Session, lat: float | None = None, lon: float | None = None) -> dict | None:
    """Latest reading from the nearest configured instrument station, with
    its distance — `is_local` says honestly whether that station is actually
    close (within `instrument_radius_km`, the same radius scoring uses to
    corroborate reports) rather than presenting a half-a-world-away demo buoy
    as if it were local sea state. Returns None only if no station exists at
    all."""
    settings = get_settings()
    ref_lat = lat if lat is not None else PILOT_CENTROID[0]
    ref_lon = lon if lon is not None else PILOT_CENTROID[1]
    nearest = _nearest_station(db, ref_lat, ref_lon)
    if nearest is None:
        return None
    distance_km = round(haversine_km(ref_lat, ref_lon, nearest.lat, nearest.lon), 1)
    return {
        "station_id": nearest.id,
        "station_name": nearest.name,
        "distance_km": distance_km,
        "is_local": distance_km <= settings.instrument_radius_km,
        **_station_sensor_summary(db, nearest.id),
    }
