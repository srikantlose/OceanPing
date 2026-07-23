"""CAP ingestion + corroboration lookup (phase 4, milestone 1) — DB-touching
glue between the pure cap_ingest.py parser and this app's data model. See
scoring/service.py for how an active advisory becomes a corroboration signal.
"""
import logging
from datetime import datetime, timezone

from shapely.geometry import Point, Polygon
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import OfficialAdvisory
from app.modules.alerts.cap_ingest import map_event_to_hazard, parse_cap
from app.modules.geo.distance import haversine_km
from app.modules.scoring.audit import append_audit

log = logging.getLogger(__name__)


def ingest_cap_document(db: Session, xml_text: str) -> list[OfficialAdvisory]:
    """Parse and store a CAP document's areas as OfficialAdvisory rows, one
    row per (info, area) pair whose <event> maps to a hazard we model.

    A message that <references> earlier identifiers (a real Cancel or Update)
    expires whatever those identifiers point to first, same as a real CAP
    consumer would — a Cancel then stores nothing further of its own; an
    Update goes on to store its own new rows same as a fresh Alert would.
    """
    parsed = parse_cap(xml_text)

    if parsed.references:
        db.execute(
            update(OfficialAdvisory)
            .where(OfficialAdvisory.cap_identifier.in_(parsed.references))
            .values(expires_at=parsed.sent)
        )

    if parsed.msg_type == "Cancel":
        db.commit()
        return []

    created: list[OfficialAdvisory] = []
    for info in parsed.infos:
        hazard_type = map_event_to_hazard(info.event)
        if hazard_type is None:
            continue
        for area in info.areas:
            row = OfficialAdvisory(
                cap_identifier=parsed.identifier,
                sender=parsed.sender,
                event=info.event,
                hazard_type=hazard_type,
                urgency=info.urgency,
                severity=info.severity,
                certainty=info.certainty,
                msg_type=parsed.msg_type,
                headline=info.headline,
                description=info.description,
                area_polygon=[[lat, lon] for lat, lon in area.polygon] if area.polygon else None,
                area_circle=(
                    {"lat": area.circle[0], "lon": area.circle[1], "radius_km": area.circle[2]}
                    if area.circle
                    else None
                ),
                effective_at=info.effective,
                expires_at=info.expires,
                raw_xml=xml_text,
            )
            db.add(row)
            created.append(row)

    db.flush()
    for row in created:
        append_audit(
            db,
            event_type="official_advisory.ingested",
            subject_type="official_advisory",
            subject_id=str(row.id),
            payload={"sender": row.sender, "event": row.event, "hazard_type": row.hazard_type},
        )
    db.commit()
    return created


def official_advisory_for(db: Session, hazard_type: str, lat: float, lon: float) -> OfficialAdvisory | None:
    """The first *active* official advisory covering this point for this
    hazard, or None. "Active" = not expired — filtered at read time, same
    posture as Alert.expires_at elsewhere in this app, so no sweep job is
    needed just to keep a time-bounded fact honest."""
    now = datetime.now(timezone.utc)
    candidates = db.scalars(
        select(OfficialAdvisory)
        .where(OfficialAdvisory.hazard_type == hazard_type)
        .where((OfficialAdvisory.expires_at.is_(None)) | (OfficialAdvisory.expires_at > now))
    ).all()
    point = Point(lon, lat)
    for advisory in candidates:
        if advisory.area_polygon:
            ring = [(p[1], p[0]) for p in advisory.area_polygon]  # (lat,lon) -> (lon,lat) for shapely
            if Polygon(ring).intersects(point):
                return advisory
        elif advisory.area_circle:
            c = advisory.area_circle
            if haversine_km(lat, lon, c["lat"], c["lon"]) <= c["radius_km"]:
                return advisory
    return None
