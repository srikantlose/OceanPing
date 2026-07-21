""""Mark Safe" check-ins (phase 3, milestone 5).

A check-in is a statement about a person, not an observation of a hazard, so
it deliberately never touches the scoring/incident path: it doesn't create a
Report, doesn't feed the confidence engine, and can't corroborate anything.
See models.py::SafetyCheckin for why that separation is structural here.

Check-ins still go through the audit chain, because "who told us they needed
help, and when did we know" is exactly the kind of question a post-event
review asks.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SafetyCheckin
from app.modules.geo.h3utils import cell_for
from app.modules.ingest.service import get_or_create_reporter
from app.modules.scoring.audit import append_audit

log = logging.getLogger(__name__)

CHECKIN_STATUSES = ("safe", "need_help")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def find_by_client_key(db: Session, client_key: str | None) -> SafetyCheckin | None:
    if not client_key:
        return None
    return db.scalar(select(SafetyCheckin).where(SafetyCheckin.client_key == client_key))


def create_checkin(
    db: Session,
    *,
    source: str,
    external_id: str,
    status: str,
    lat: float,
    lon: float,
    note: str | None = None,
    observed_at: datetime | None = None,
    client_key: str | None = None,
) -> SafetyCheckin:
    if status not in CHECKIN_STATUSES:
        raise ValueError(f"status must be one of {CHECKIN_STATUSES}")
    reporter = get_or_create_reporter(db, source, external_id)
    observed_at = observed_at or _utcnow()
    checkin = SafetyCheckin(
        reporter_id=reporter.id,
        status=status,
        lat=lat,
        lon=lon,
        geom=f"SRID=4326;POINT({lon} {lat})",
        h3_cell=cell_for(lat, lon),
        note=note,
        client_key=client_key,
        observed_at=observed_at,
    )
    db.add(checkin)
    db.flush()
    append_audit(
        db,
        event_type="safety.checkin",
        subject_type="safety_checkin",
        subject_id=str(checkin.id),
        payload={
            "status": status,
            "h3_cell": checkin.h3_cell,
            "source": source,
            "observed_at": observed_at.isoformat(),
        },
    )
    db.commit()
    return checkin


def recent_checkins(db: Session, hours: float = 48.0, status: str | None = None) -> list[SafetyCheckin]:
    """Analyst view: check-ins observed within the window, newest first.
    Filtered by status so a responder can pull just the need_help list."""
    since = _utcnow() - timedelta(hours=hours)
    stmt = (
        select(SafetyCheckin)
        .where(SafetyCheckin.observed_at >= since)
        .order_by(SafetyCheckin.observed_at.desc())
    )
    if status is not None:
        stmt = stmt.where(SafetyCheckin.status == status)
    return list(db.scalars(stmt).all())


def checkin_counts(db: Session, hours: float = 48.0) -> dict:
    rows = recent_checkins(db, hours=hours)
    return {
        "window_hours": hours,
        "total": len(rows),
        "safe": sum(1 for r in rows if r.status == "safe"),
        "need_help": sum(1 for r in rows if r.status == "need_help"),
    }
