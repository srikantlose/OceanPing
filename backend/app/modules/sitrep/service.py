"""Builds the verified-data snapshot an auto-SITREP is drafted from, and the
generate/file lifecycle around it.

Generation is normally hourly (see core/scheduler.py); this module is also
reachable on demand (analyst "generate now", the drill) via the same
`generate_sitrep()` entry point. Filing is the analyst's one-click review
action — it never edits the drafted content, only marks it reviewed.
"""
import hashlib
import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from shapely.geometry import shape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Alert, Incident, Report, Shelter, Sitrep
from app.modules.geo.distance import haversine_km
from app.modules.geo.hotspots import compute_hotspots
from app.modules.scoring.audit import append_audit, verify_chain
from app.modules.sitrep import engine

log = logging.getLogger(__name__)

HOTSPOT_MATCH_KM = 3.0  # same dominant hazard within this radius = "the same" hotspot across periods


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _report_counts(db: Session, period_start: datetime, period_end: datetime) -> dict:
    rows = db.execute(
        select(Report.status, Report.hazard_type)
        .where(Report.created_at >= period_start)
        .where(Report.created_at < period_end)
    ).all()
    return {
        "total": len(rows),
        "by_status": dict(Counter(r.status for r in rows)),
        "by_hazard": dict(Counter(r.hazard_type for r in rows)),
    }


def _incident_counts(db: Session, period_start: datetime, period_end: datetime) -> dict:
    """Incidents touched (merged a report) during the period. "New" is the
    subset first seen inside the same window — one query, split in Python,
    rather than a second round trip."""
    touched = db.scalars(
        select(Incident).where(Incident.last_seen >= period_start).where(Incident.last_seen < period_end)
    ).all()
    new_count = sum(1 for i in touched if period_start <= i.first_seen < period_end)
    return {
        "active_in_period": len(touched),
        "new": new_count,
        "by_status": dict(Counter(i.status for i in touched)),
    }


def _alerts_summary(db: Session, period_start: datetime, period_end: datetime) -> dict:
    issued = db.scalars(
        select(Alert)
        .where(Alert.created_at >= period_start)
        .where(Alert.created_at < period_end)
        .order_by(Alert.created_at)
    ).all()
    active_now = db.scalars(select(Alert).where(Alert.status == "active")).all()
    return {
        "issued": [
            {
                "tier": a.tier,
                "hazard_type": a.hazard_type,
                "issued_by": a.issued_by or "automatic",
                "created_at": a.created_at.isoformat(),
            }
            for a in issued
        ],
        "active_now": [
            {"tier": a.tier, "hazard_type": a.hazard_type, "issued_by": a.issued_by or "automatic"}
            for a in active_now
        ],
    }


def _resources_summary(db: Session) -> dict:
    shelters = db.scalars(select(Shelter)).all()
    open_shelters = [s for s in shelters if s.status == "open"]
    known_capacity = [s.capacity for s in open_shelters if s.capacity is not None]
    return {
        "shelters_total": len(shelters),
        "shelters_open": len(open_shelters),
        "open_capacity_total": sum(known_capacity),
        "open_capacity_unknown_count": len(open_shelters) - len(known_capacity),
    }


def _audit_summary(db: Session) -> dict:
    intact, checked = verify_chain(db)
    return {"chain_intact": intact, "entries_checked": checked}


def _hotspot_snapshot(geojson: dict) -> list[dict]:
    out = []
    for f in geojson["features"]:
        centroid = shape(f["geometry"]).centroid
        out.append(
            {
                "lat": round(centroid.y, 5),
                "lon": round(centroid.x, 5),
                "report_count": f["properties"]["report_count"],
                "dominant_hazard": f["properties"]["dominant_hazard"],
                "intensity": f["properties"]["intensity"],
            }
        )
    return out


def _match_hotspot(cur: dict, previous: list[dict], matched: set[int]) -> int | None:
    for i, prev in enumerate(previous):
        if i in matched:
            continue
        if (
            prev["dominant_hazard"] == cur["dominant_hazard"]
            and haversine_km(cur["lat"], cur["lon"], prev["lat"], prev["lon"]) <= HOTSPOT_MATCH_KM
        ):
            return i
    return None


def _hotspot_movement(db: Session, previous: list[dict]) -> dict:
    """Tags each current hotspot as "new" or "persisting" against the
    previous SITREP's hotspot snapshot (matched by dominant hazard + proximity),
    and lists previous hotspots that cleared (no longer present)."""
    current = _hotspot_snapshot(compute_hotspots(db))
    matched: set[int] = set()
    tagged = []
    for cur in current:
        match = _match_hotspot(cur, previous, matched)
        if match is not None:
            matched.add(match)
            tagged.append({**cur, "movement": "persisting"})
        else:
            tagged.append({**cur, "movement": "new"})
    cleared = [p for i, p in enumerate(previous) if i not in matched]
    return {"current": current, "tagged": tagged, "cleared": cleared}


def build_snapshot(
    db: Session, period_start: datetime, period_end: datetime, previous_hotspots: list[dict] | None = None
) -> dict:
    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "reports": _report_counts(db, period_start, period_end),
        "incidents": _incident_counts(db, period_start, period_end),
        "alerts": _alerts_summary(db, period_start, period_end),
        "hotspots": _hotspot_movement(db, previous_hotspots or []),
        "resources": _resources_summary(db),
        "audit": _audit_summary(db),
    }


def snapshot_hash(snapshot: dict) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def generate_sitrep(db: Session) -> Sitrep:
    settings = get_settings()
    period_end = _utcnow()
    last = db.scalars(select(Sitrep).order_by(Sitrep.generated_at.desc()).limit(1)).first()
    period_start = last.period_end if last else period_end - timedelta(hours=settings.sitrep_period_hours)
    previous_hotspots = (
        last.content.get("sections", {}).get("hotspots", {}).get("current") if last else None
    ) or []

    snapshot = build_snapshot(db, period_start, period_end, previous_hotspots)
    draft = engine.build_sitrep(snapshot)
    data_hash = snapshot_hash(snapshot)

    sitrep = Sitrep(
        period_start=period_start,
        period_end=period_end,
        status="draft",
        content=draft,
        data_snapshot_hash=data_hash,
    )
    db.add(sitrep)
    db.flush()
    append_audit(
        db,
        event_type="sitrep.generated",
        subject_type="sitrep",
        subject_id=str(sitrep.id),
        payload={
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "data_snapshot_hash": data_hash,
        },
    )
    db.commit()
    log.info("Generated SITREP %s for %s - %s", sitrep.id, period_start, period_end)
    return sitrep


def file_sitrep(db: Session, sitrep: Sitrep, analyst: str) -> Sitrep:
    if sitrep.status == "filed":
        raise ValueError("SITREP already filed")
    sitrep.status = "filed"
    sitrep.filed_by = analyst
    sitrep.filed_at = _utcnow()
    append_audit(
        db,
        event_type="sitrep.filed",
        subject_type="sitrep",
        subject_id=str(sitrep.id),
        payload={"analyst": analyst, "data_snapshot_hash": sitrep.data_snapshot_hash},
    )
    db.commit()
    return sitrep
