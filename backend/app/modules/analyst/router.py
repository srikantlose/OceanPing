"""Analyst-only API: login, full-detail report queue (exact coordinates,
confidence breakdown), verify/reject actions, incidents, audit chain."""
import hmac
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import issue_token, require_analyst
from app.models import AuditLog, Incident, MediaAsset, Report
from app.modules.scoring.audit import verify_chain
from app.modules.scoring.service import apply_verification, rescore_report

router = APIRouter(tags=["analyst"])


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn) -> dict:
    settings = get_settings()
    user_ok = hmac.compare_digest(body.username, settings.analyst_username)
    pass_ok = hmac.compare_digest(body.password, settings.analyst_password)
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"token": issue_token(body.username)}


def _report_detail(report: Report) -> dict:
    return {
        "id": str(report.id),
        "created_at": report.created_at.isoformat(),
        "lat": report.lat,
        "lon": report.lon,
        "h3_cell": report.h3_cell,
        "hazard_type": report.hazard_type,
        "urgency": report.urgency,
        "text": report.text,
        "lang": report.lang,
        "source": report.source,
        "status": report.status,
        "confidence": report.confidence,
        "confidence_components": report.confidence_components,
        "incident_id": str(report.incident_id) if report.incident_id else None,
        "reporter": {
            "trust_score": round(report.reporter.trust_score, 3),
            "verified_count": report.reporter.verified_count,
            "debunked_count": report.reporter.debunked_count,
            "source": report.reporter.source,
        },
        "media": [{"id": str(m.id), "exif": m.exif} for m in report.media],
    }


@router.get("/analyst/reports")
def list_reports(
    status: str | None = None,
    limit: int = 100,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    q = select(Report).order_by(Report.created_at.desc()).limit(min(limit, 500))
    if status:
        q = q.where(Report.status == status)
    return [_report_detail(r) for r in db.scalars(q).all()]


class DecisionIn(BaseModel):
    note: str | None = None


@router.post("/analyst/reports/{report_id}/verify")
def decide_verify(
    report_id: uuid.UUID,
    body: DecisionIn,
    analyst: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    return _decide(report_id, "verify", body, analyst, db)


@router.post("/analyst/reports/{report_id}/reject")
def decide_reject(
    report_id: uuid.UUID,
    body: DecisionIn,
    analyst: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    return _decide(report_id, "reject", body, analyst, db)


def _decide(report_id: uuid.UUID, action: str, body: DecisionIn, analyst: str, db: Session) -> dict:
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.status in ("verified", "rejected"):
        raise HTTPException(status_code=409, detail=f"Report already {report.status}")
    apply_verification(db, report, analyst=analyst, action=action, note=body.note)
    return _report_detail(report)


@router.post("/analyst/reports/{report_id}/rescore")
def rescore(
    report_id: uuid.UUID,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    rescore_report(db, report)
    db.commit()
    return _report_detail(report)


@router.get("/analyst/incidents")
def list_incidents(
    limit: int = 50,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    incidents = db.scalars(
        select(Incident).order_by(Incident.last_seen.desc()).limit(min(limit, 200))
    ).all()
    return [
        {
            "id": str(inc.id),
            "hazard_type": inc.hazard_type,
            "status": inc.status,
            "report_count": inc.report_count,
            "max_confidence": inc.max_confidence,
            "centroid": [inc.centroid_lat, inc.centroid_lon],
            "h3_cells": inc.h3_cells,
            "first_seen": inc.first_seen.isoformat(),
            "last_seen": inc.last_seen.isoformat(),
            "reports": [_report_detail(r) for r in inc.reports],
        }
        for inc in incidents
    ]


@router.get("/analyst/audit")
def audit_entries(
    limit: int = 100,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    entries = db.scalars(
        select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500))
    ).all()
    return [
        {
            "id": e.id,
            "ts": e.ts.isoformat(),
            "event_type": e.event_type,
            "subject_type": e.subject_type,
            "subject_id": e.subject_id,
            "payload": e.payload,
            "hash": e.hash,
        }
        for e in entries
    ]


@router.get("/analyst/audit/verify")
def audit_verify(_: str = Depends(require_analyst), db: Session = Depends(get_db)) -> dict:
    intact, checked = verify_chain(db)
    return {"intact": intact, "entries_checked": checked}


@router.get("/analyst/media/{media_id}")
def media_file(
    media_id: uuid.UUID,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> FileResponse:
    asset = db.get(MediaAsset, media_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(asset.path)
