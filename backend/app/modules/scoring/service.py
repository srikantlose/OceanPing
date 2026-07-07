"""Scoring orchestration: gather live components, rescore reports, enforce the
escalation gate, run the trust ladder on analyst decisions."""
import logging
from datetime import datetime, timedelta, timezone

import h3
from geoalchemy2 import Geography
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Report, Reporter, Station, StationAnomaly, Verification
from app.modules.scoring import engine
from app.modules.scoring.audit import append_audit

log = logging.getLogger(__name__)

TRUST_STEP_UP = 0.05
TRUST_STEP_DOWN = 0.10
TRUST_MAX = 0.95
TRUST_MIN = 0.05


def _coherence_count(db: Session, report: Report) -> int:
    """Distinct other reporters, same hazard, within 1 H3 ring and ±30 min."""
    settings = get_settings()
    window = timedelta(minutes=settings.coherence_minutes)
    cells = list(h3.grid_disk(report.h3_cell, 1))
    return (
        db.scalar(
            select(func.count(func.distinct(Report.reporter_id)))
            .where(Report.id != report.id)
            .where(Report.reporter_id != report.reporter_id)
            .where(Report.hazard_type == report.hazard_type)
            .where(Report.h3_cell.in_(cells))
            .where(Report.created_at.between(report.created_at - window, report.created_at + window))
            .where(Report.status != "rejected")
        )
        or 0
    )


def _instrument_zscores(db: Session, report: Report) -> list[dict]:
    """Active, hazard-consistent anomalies at stations within the radius."""
    settings = get_settings()
    allowed = engine.HAZARD_VARIABLES.get(report.hazard_type, set())
    if not allowed:
        return []
    point = func.ST_SetSRID(func.ST_MakePoint(report.lon, report.lat), 4326)
    nearby_ids = [
        row[0]
        for row in db.execute(
            select(Station.id).where(
                func.ST_DWithin(
                    Station.geom.cast(Geography),
                    point.cast(Geography),
                    settings.instrument_radius_km * 1000,
                )
            )
        )
    ]
    if not nearby_ids:
        return []
    anomalies = db.scalars(
        select(StationAnomaly)
        .where(StationAnomaly.active.is_(True))
        .where(StationAnomaly.station_id.in_(nearby_ids))
        .where(StationAnomaly.variable.in_(list(allowed)))
    ).all()
    return [
        {"station_id": a.station_id, "variable": a.variable, "zscore": round(a.zscore, 3)}
        for a in anomalies
    ]


def rescore_report(db: Session, report: Report) -> float:
    """Recompute confidence; escalate unverified→corroborated only with
    instrument agreement (the no-citizen-only-escalation rule). Flushes, no commit."""
    settings = get_settings()
    n_independent = _coherence_count(db, report)
    anomalies = _instrument_zscores(db, report)
    prev = report.confidence_components or {}
    components = {
        "trust": round(report.reporter.trust_score, 4),
        "coherence": engine.coherence_score(n_independent),
        "instrument": engine.instrument_score([a["zscore"] for a in anomalies]),
        "media": prev.get("media", engine.MEDIA_NEUTRAL),
    }
    detail = {
        "n_independent_reports": n_independent,
        "corroborating_anomalies": anomalies,
        # forensics are computed once at ingest; carry them across rescores
        "media_forensics": prev.get("media_forensics") or (prev.get("detail") or {}).get("media_forensics"),
    }
    old_confidence = report.confidence
    new_confidence = engine.combine(components)
    report.confidence = new_confidence
    report.confidence_components = {**components, "detail": detail}

    if (
        report.status == "unverified"
        and new_confidence >= settings.corroborated_threshold
        and components["instrument"] > 0
    ):
        report.status = "corroborated"
        append_audit(
            db,
            event_type="report.status_changed",
            subject_type="report",
            subject_id=str(report.id),
            payload={"from": "unverified", "to": "corroborated", "confidence": new_confidence},
        )

    if abs(new_confidence - old_confidence) >= 0.01:
        append_audit(
            db,
            event_type="report.rescored",
            subject_type="report",
            subject_id=str(report.id),
            payload={"from": old_confidence, "to": new_confidence, "components": components},
        )

    inc = report.incident
    if inc is not None:
        inc.max_confidence = max(inc.max_confidence, new_confidence)
        if report.status == "corroborated" and inc.status == "unverified":
            inc.status = "corroborated"
    db.flush()
    return new_confidence


def rescore_recent(db: Session) -> int:
    """Periodic catch-all: rescore recent non-final reports (scheduler job)."""
    settings = get_settings()
    since = datetime.now(timezone.utc) - timedelta(hours=settings.incident_window_hours)
    reports = db.scalars(
        select(Report)
        .where(Report.created_at >= since)
        .where(Report.status.in_(["unverified", "corroborated"]))
    ).all()
    for report in reports:
        rescore_report(db, report)
    db.commit()
    return len(reports)


def apply_verification(
    db: Session, report: Report, analyst: str, action: str, note: str | None = None
) -> Report:
    """Analyst decision: the only path to 'verified'. Runs the trust ladder."""
    if action not in ("verify", "reject"):
        raise ValueError(f"Unknown verification action: {action}")
    old_status = report.status
    reporter: Reporter = report.reporter
    if action == "verify":
        report.status = "verified"
        reporter.trust_score = min(TRUST_MAX, reporter.trust_score + TRUST_STEP_UP)
        reporter.verified_count += 1
        if report.incident is not None:
            report.incident.status = "verified"
    else:
        report.status = "rejected"
        reporter.trust_score = max(TRUST_MIN, reporter.trust_score - TRUST_STEP_DOWN)
        reporter.debunked_count += 1

    db.add(Verification(report_id=report.id, analyst=analyst, action=action, note=note))
    append_audit(
        db,
        event_type="report.status_changed",
        subject_type="report",
        subject_id=str(report.id),
        payload={
            "from": old_status,
            "to": report.status,
            "analyst": analyst,
            "action": action,
            "note": note,
            "reporter_trust_after": round(reporter.trust_score, 4),
        },
    )
    db.commit()
    return report
