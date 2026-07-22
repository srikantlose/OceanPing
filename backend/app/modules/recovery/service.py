"""Recovery module DB I/O (phase 3, milestone 7): damage assessments, the
mutual-aid board, and the missing/found-person registry. See engine.py for
the pure matching logic and cv.py for the damage-photo triage this delegates
to."""
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    RELIEF_CATEGORIES,
    MISSING_PERSON_TYPES,
    AidOffer,
    DamageAssessment,
    MissingPerson,
    ReliefRequest,
)
from app.modules.geo.h3utils import cell_centroid, cell_for
from app.modules.ingest import media as media_mod
from app.modules.ingest.service import get_or_create_reporter
from app.modules.recovery import cv
from app.modules.recovery.engine import (
    AidParty,
    MissingCandidate,
    match_aid,
    rank_missing_matches,
)
from app.modules.scoring.audit import append_audit

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- damage assessment ------------------------------------------------------


def submit_damage_assessment(
    db: Session,
    *,
    source: str,
    external_id: str,
    lat: float,
    lon: float,
    media_bytes: bytes,
    media_filename: str | None,
    note: str | None = None,
) -> DamageAssessment:
    """A damage assessment *is* the photo — unlike a hazard Report, where
    media is optional corroboration, there's nothing to triage without one."""
    reporter = get_or_create_reporter(db, source, external_id)
    path = media_mod.save_upload(media_bytes, media_filename)
    phash = media_mod.compute_phash(path)
    result = cv.classify_damage(path)
    assessment = DamageAssessment(
        reporter_id=reporter.id,
        lat=lat,
        lon=lon,
        geom=f"SRID=4326;POINT({lon} {lat})",
        h3_cell=cell_for(lat, lon),
        photo_path=str(path),
        phash=phash,
        damage_class=result.damage_class,
        severity=result.severity,
        cv_confidence=result.confidence,
        cv_mode=result.mode,
        cv_detail=result.detail,
        status="submitted",
        note=note,
    )
    db.add(assessment)
    db.flush()
    append_audit(
        db,
        event_type="recovery.damage_assessed",
        subject_type="damage_assessment",
        subject_id=str(assessment.id),
        payload={
            "damage_class": result.damage_class,
            "severity": result.severity,
            "cv_mode": result.mode,
            "h3_cell": assessment.h3_cell,
        },
    )
    db.commit()
    return assessment


def recent_damage_assessments(db: Session, hours: float = 72.0) -> list[DamageAssessment]:
    since = _utcnow() - timedelta(hours=hours)
    return list(
        db.scalars(
            select(DamageAssessment)
            .where(DamageAssessment.created_at >= since)
            .order_by(DamageAssessment.created_at.desc())
        ).all()
    )


def mark_damage_reviewed(db: Session, assessment: DamageAssessment, *, analyst: str) -> DamageAssessment:
    assessment.status = "reviewed"
    append_audit(
        db,
        event_type="recovery.damage_reviewed",
        subject_type="damage_assessment",
        subject_id=str(assessment.id),
        payload={"analyst": analyst},
    )
    db.commit()
    return assessment


def public_damage_features(db: Session, hours: float = 72.0) -> dict:
    """Public damage-map layer, fuzzed to H3 cell centroid — same privacy
    convention as /map/reports. Damage assessments carry no reporter PII (a
    photo of a place, not a person), so this is safe to expose in full detail
    otherwise."""
    features = []
    for a in recent_damage_assessments(db, hours=hours):
        lat, lon = cell_centroid(a.h3_cell)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "id": str(a.id),
                    "damage_class": a.damage_class,
                    "severity": a.severity,
                    "cv_confidence": a.cv_confidence,
                    "created_at": a.created_at.isoformat(),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


# --- mutual-aid board --------------------------------------------------------


def create_relief_request(
    db: Session,
    *,
    source: str,
    external_id: str,
    lat: float,
    lon: float,
    category: str,
    description: str | None = None,
    people_count: int | None = None,
) -> ReliefRequest:
    if category not in RELIEF_CATEGORIES:
        raise ValueError(f"category must be one of {RELIEF_CATEGORIES}")
    reporter = get_or_create_reporter(db, source, external_id)
    req = ReliefRequest(
        reporter_id=reporter.id,
        lat=lat,
        lon=lon,
        geom=f"SRID=4326;POINT({lon} {lat})",
        h3_cell=cell_for(lat, lon),
        category=category,
        description=description,
        people_count=people_count,
        status="open",
    )
    db.add(req)
    db.commit()
    return req


def create_aid_offer(
    db: Session,
    *,
    source: str,
    external_id: str,
    lat: float,
    lon: float,
    category: str,
    description: str | None = None,
    capacity: int | None = None,
) -> AidOffer:
    if category not in RELIEF_CATEGORIES:
        raise ValueError(f"category must be one of {RELIEF_CATEGORIES}")
    reporter = get_or_create_reporter(db, source, external_id)
    offer = AidOffer(
        reporter_id=reporter.id,
        lat=lat,
        lon=lon,
        geom=f"SRID=4326;POINT({lon} {lat})",
        h3_cell=cell_for(lat, lon),
        category=category,
        description=description,
        capacity=capacity,
        status="open",
    )
    db.add(offer)
    db.commit()
    return offer


def open_relief_requests(db: Session) -> list[ReliefRequest]:
    return list(
        db.scalars(
            select(ReliefRequest).where(ReliefRequest.status == "open").order_by(ReliefRequest.created_at.desc())
        ).all()
    )


def open_aid_offers(db: Session) -> list[AidOffer]:
    return list(
        db.scalars(select(AidOffer).where(AidOffer.status == "open").order_by(AidOffer.created_at.desc())).all()
    )


def fulfill_relief_request(db: Session, req: ReliefRequest, *, analyst: str, fulfilled_by: str | None = None) -> ReliefRequest:
    req.status = "fulfilled"
    req.fulfilled_by = fulfilled_by or analyst
    req.fulfilled_at = _utcnow()
    append_audit(
        db,
        event_type="recovery.relief_fulfilled",
        subject_type="relief_request",
        subject_id=str(req.id),
        payload={"analyst": analyst, "fulfilled_by": req.fulfilled_by},
    )
    db.commit()
    return req


def close_aid_offer(db: Session, offer: AidOffer, *, analyst: str) -> AidOffer:
    offer.status = "closed"
    append_audit(
        db,
        event_type="recovery.aid_offer_closed",
        subject_type="aid_offer",
        subject_id=str(offer.id),
        payload={"analyst": analyst},
    )
    db.commit()
    return offer


def suggested_aid_matches(db: Session) -> list[dict]:
    """Candidate (request, offer) pairs over currently-open rows — computed
    fresh on every call rather than a background job, since pilot volumes are
    small (see engine.py::match_aid)."""
    settings = get_settings()
    requests = [AidParty(id=str(r.id), category=r.category, lat=r.lat, lon=r.lon) for r in open_relief_requests(db)]
    offers = [AidParty(id=str(o.id), category=o.category, lat=o.lat, lon=o.lon) for o in open_aid_offers(db)]
    matches = match_aid(requests, offers, settings.recovery_mutual_aid_max_km)
    return [
        {
            "request_id": m.request_id,
            "offer_id": m.offer_id,
            "category": m.category,
            "distance_km": m.distance_km,
        }
        for m in matches
    ]


# --- missing/found-person registry ------------------------------------------


def create_missing_person(
    db: Session,
    *,
    source: str,
    external_id: str,
    report_type: str,
    name: str,
    age: int | None = None,
    gender: str | None = None,
    description: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    media_bytes: bytes | None = None,
    media_filename: str | None = None,
) -> MissingPerson:
    if report_type not in MISSING_PERSON_TYPES:
        raise ValueError(f"report_type must be one of {MISSING_PERSON_TYPES}")
    reporter = get_or_create_reporter(db, source, external_id)
    photo_path = None
    if media_bytes:
        photo_path = str(media_mod.save_upload(media_bytes, media_filename))
    person = MissingPerson(
        reporter_id=reporter.id,
        report_type=report_type,
        name=name,
        age=age,
        gender=gender,
        description=description,
        lat=lat,
        lon=lon,
        h3_cell=cell_for(lat, lon) if lat is not None and lon is not None else None,
        photo_path=photo_path,
        status="open",
    )
    db.add(person)
    db.flush()
    # Deliberately no name/description in the audit payload — a hash-chained
    # log that outlives this table's retention window shouldn't become the
    # thing that keeps a vulnerable person's details around forever.
    append_audit(
        db,
        event_type="recovery.missing_reported",
        subject_type="missing_person",
        subject_id=str(person.id),
        payload={"report_type": report_type, "h3_cell": person.h3_cell, "has_photo": photo_path is not None},
    )
    db.commit()
    return person


def open_missing_persons(db: Session, report_type: str | None = None) -> list[MissingPerson]:
    stmt = select(MissingPerson).where(MissingPerson.status == "open").order_by(MissingPerson.created_at.desc())
    if report_type is not None:
        stmt = stmt.where(MissingPerson.report_type == report_type)
    return list(db.scalars(stmt).all())


def candidate_matches_for(db: Session, person: MissingPerson) -> list[dict]:
    """Open rows of the *opposite* report_type, ranked by fuzzy name score
    (and geo-gated when both sides carry a location) — see
    engine.py::rank_missing_matches. A suggestion list only; resolving one is
    always an analyst action (see resolve_missing_person)."""
    settings = get_settings()
    opposite = "found" if person.report_type == "missing" else "missing"
    pool = [p for p in open_missing_persons(db, report_type=opposite) if p.id != person.id]
    candidates = [MissingCandidate(id=str(p.id), name=p.name, lat=p.lat, lon=p.lon) for p in pool]
    ranked = rank_missing_matches(
        person.name,
        person.lat,
        person.lon,
        candidates,
        name_threshold=settings.recovery_missing_match_threshold,
        max_km=settings.recovery_missing_match_max_km,
    )
    by_id = {str(p.id): p for p in pool}
    out = []
    for m in ranked:
        cand = by_id[m.candidate_id]
        out.append(
            {
                "candidate_id": m.candidate_id,
                "candidate_name": cand.name,
                "name_score": m.name_score,
                "distance_km": m.distance_km,
            }
        )
    return out


def resolve_missing_person(
    db: Session,
    person: MissingPerson,
    *,
    analyst: str,
    matched_person_id=None,
) -> MissingPerson:
    if person.status != "open":
        raise ValueError("Already resolved")
    now = _utcnow()
    person.status = "resolved"
    person.resolved_by = analyst
    person.resolved_at = now
    matched = False
    if matched_person_id is not None:
        other = db.get(MissingPerson, matched_person_id)
        if other is None:
            raise ValueError("Matched person not found")
        if other.report_type == person.report_type:
            raise ValueError("A missing report can only resolve against a found report, or vice versa")
        if other.status != "open":
            raise ValueError("Matched person is already resolved")
        person.matched_person_id = other.id
        other.matched_person_id = person.id
        other.status = "resolved"
        other.resolved_by = analyst
        other.resolved_at = now
        matched = True
    append_audit(
        db,
        event_type="recovery.missing_resolved",
        subject_type="missing_person",
        subject_id=str(person.id),
        payload={"analyst": analyst, "matched": matched},
    )
    db.commit()
    return person


def purge_expired_missing_persons(db: Session) -> int:
    """Privacy retention (phase 3, milestone 7's "strict privacy... retention
    limit"): a real scheduled job (see core/scheduler.py), not just a
    documented policy. Applies uniformly regardless of resolution status —
    this registry's job is done within months, and indefinite retention of a
    name/description/photo of a vulnerable person is the actual risk being
    managed here; a case still open after the retention window is an
    operational escalation this pilot table isn't the right place to track
    forever. Removes the photo file too, not just the row."""
    settings = get_settings()
    cutoff = _utcnow() - timedelta(days=settings.recovery_missing_person_retention_days)
    expired = list(db.scalars(select(MissingPerson).where(MissingPerson.created_at < cutoff)).all())
    expired_ids = {p.id for p in expired}
    if expired_ids:
        # matched_person_id has no ondelete=CASCADE, so any row (expired or
        # not) pointing into the expired set must be unlinked before the
        # DELETEs run, in either direction.
        referencing = db.scalars(
            select(MissingPerson).where(MissingPerson.matched_person_id.in_(expired_ids))
        ).all()
        for row in referencing:
            row.matched_person_id = None
        db.flush()
    for person in expired:
        if person.photo_path:
            try:
                Path(person.photo_path).unlink(missing_ok=True)
            except OSError:
                log.warning("Could not remove expired missing-person photo %s", person.photo_path, exc_info=True)
        db.delete(person)
    if expired:
        append_audit(
            db,
            event_type="recovery.missing_purged",
            subject_type="missing_person",
            subject_id="batch",
            payload={"count": len(expired), "retention_days": settings.recovery_missing_person_retention_days},
        )
    db.commit()
    return len(expired)
