"""Scoring orchestration: gather live components, rescore reports, enforce the
escalation gate, run the trust ladder on analyst decisions."""
import logging
from datetime import datetime, timedelta, timezone

import h3
from geoalchemy2 import Geography
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.redisclient import get_redis
from app.models import (
    HAZARD_TYPES,
    Report,
    Reporter,
    SatelliteObservation,
    Station,
    StationAnomaly,
    TrainingExample,
    Verification,
)
from app.modules.alerts.cap_service import official_advisory_for
from app.modules.alerts.service import sync_incident_alert
from app.modules.nlp import classifier
from app.modules.satellite.providers import HAZARD_RECIPES
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


def instrument_zscores_near(db: Session, hazard_type: str, lat: float, lon: float) -> list[dict]:
    """Active, hazard-consistent anomalies at stations within the radius of a
    point. Generalized from a single report's location so other modules (the
    rumor tracker's contradiction check, in particular) can ask "does
    instrument data corroborate this hazard claim here" without querying
    Station/StationAnomaly directly themselves."""
    settings = get_settings()
    allowed = engine.HAZARD_VARIABLES.get(hazard_type, set())
    if not allowed:
        return []
    point = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)
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


def _instrument_zscores(db: Session, report: Report) -> list[dict]:
    return instrument_zscores_near(db, report.hazard_type, report.lat, report.lon)


def _satellite_observations(db: Session, report: Report) -> list[dict]:
    """Scene observations recorded for this report's incident under its
    hazard's recipe (see satellite/providers.py). Empty for hazards with no
    recipe, or before the (hours-latency) satellite poll job has run."""
    if report.incident_id is None:
        return []
    recipe = HAZARD_RECIPES.get(report.hazard_type)
    if not recipe:
        return []
    observations = db.scalars(
        select(SatelliteObservation)
        .where(SatelliteObservation.incident_id == report.incident_id)
        .where(SatelliteObservation.recipe == recipe)
    ).all()
    return [
        {
            "provider": o.provider,
            "recipe": o.recipe,
            "score": round(o.score, 4),
            "scene_time": o.scene_time.isoformat(),
            "scene_url": o.scene_url,
        }
        for o in observations
    ]


def _official_advisory(db: Session, report: Report) -> dict | None:
    """An active official CAP advisory (phase 4, milestone 1 — see
    alerts/cap_ingest.py + cap_service.py) covering this report's hazard and
    location, or None. Feeds engine.official_score() (the seven-signal
    rebalance) and, like instrument/satellite, also gates escalation to
    "corroborated" below — the same non-citizen-controlled role they play,
    not a replacement for either."""
    advisory = official_advisory_for(db, report.hazard_type, report.lat, report.lon)
    if advisory is None:
        return None
    return {
        "id": str(advisory.id),
        "sender": advisory.sender,
        "event": advisory.event,
        "severity": advisory.severity,
        "certainty": advisory.certainty,
    }


def _account_device_score(report: Report) -> float:
    """Reporter age + recent-report burst, reusing the same Redis counter
    ingest/service.py's rate limiter already increments — no new I/O path."""
    reporter = report.reporter
    age_hours = max(0.0, (report.created_at - reporter.created_at).total_seconds() / 3600.0)
    try:
        raw = get_redis().get(f"rl:rep:{reporter.external_id_hash}")
        recent_count = int(raw) if raw is not None else 1
    except Exception:
        recent_count = 1
    return engine.account_device_score(age_hours, recent_count)


def rescore_report(db: Session, report: Report) -> float:
    """Recompute confidence; escalate unverified→corroborated only with
    instrument, satellite, or official-advisory agreement (the no-citizen-
    only-escalation rule — report volume/trust/coherence alone still never
    gets there). Satellite only has a recipe for a handful of slow hazards
    (oil_spill, algal_bloom, coastal_flooding, storm_surge — see
    HAZARD_RECIPES), so fast hazards like tsunami/rip_current gate on
    instrument or official alone, same as before official existed.
    Flushes, no commit."""
    settings = get_settings()
    n_independent = _coherence_count(db, report)
    anomalies = _instrument_zscores(db, report)
    hearsay = classifier.detect_hearsay(report.text)
    satellite_observations = _satellite_observations(db, report)
    official_advisory = _official_advisory(db, report)
    prev = report.confidence_components or {}
    components = {
        "trust": round(report.reporter.trust_score, 4),
        "coherence": engine.coherence_score(n_independent, hearsay=hearsay),
        "instrument": engine.instrument_score([a["zscore"] for a in anomalies]),
        "media": prev.get("media", engine.MEDIA_NEUTRAL),
        "satellite": engine.satellite_score([o["score"] for o in satellite_observations]),
        "account_device": _account_device_score(report),
        "official": engine.official_score(official_advisory),
    }
    detail = {
        "n_independent_reports": n_independent,
        "corroborating_anomalies": anomalies,
        "hearsay": hearsay,
        "satellite_observations": satellite_observations,
        "official_advisory": official_advisory,
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
        and (components["instrument"] > 0 or components["satellite"] > 0 or components["official"] > 0)
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
        try:
            sync_incident_alert(db, inc)
        except Exception:
            log.exception("Alert sync failed for incident %s", inc.id)
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
    db: Session,
    report: Report,
    analyst: str,
    action: str,
    note: str | None = None,
    corrected_hazard_type: str | None = None,
) -> Report:
    """Analyst decision: the only path to 'verified'. Runs the trust ladder.

    corrected_hazard_type is the reject-flow's "wrong hazard type? which?" answer:
    the report itself still ends up rejected (it doesn't get published either way),
    but this makes the training_examples row a usable corrected label instead of a
    bare negative — see retrain.py::export_examples()."""
    if action not in ("verify", "reject"):
        raise ValueError(f"Unknown verification action: {action}")
    if corrected_hazard_type is not None and corrected_hazard_type not in HAZARD_TYPES:
        raise ValueError(f"Unknown hazard type: {corrected_hazard_type}")
    old_status = report.status
    reporter: Reporter = report.reporter
    if action == "verify":
        report.status = "verified"
        reporter.trust_score = min(TRUST_MAX, reporter.trust_score + TRUST_STEP_UP)
        reporter.verified_count += 1
        if report.incident is not None:
            report.incident.status = "verified"
            db.flush()
            try:
                sync_incident_alert(db, report.incident)
            except Exception:
                log.exception("Alert sync failed for incident %s", report.incident.id)
    else:
        report.status = "rejected"
        reporter.trust_score = max(TRUST_MIN, reporter.trust_score - TRUST_STEP_DOWN)
        reporter.debunked_count += 1

    db.add(Verification(report_id=report.id, analyst=analyst, action=action, note=note))
    db.add(
        TrainingExample(
            report_id=report.id,
            text=report.text or "",
            lang=report.lang,
            hazard_type=report.hazard_type,
            outcome=action,
            corrected_hazard_type=corrected_hazard_type,
        )
    )
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
            "corrected_hazard_type": corrected_hazard_type,
            "reporter_trust_after": round(reporter.trust_score, 4),
        },
    )
    db.commit()
    return report
