"""Analyst-only rumor-tracker endpoints. Detection is normally automatic
(scheduled, see core/scheduler.py) — this exposes the same `detect_narratives`
for an analyst who doesn't want to wait for the next tick (and for the drill),
plus the approve/dismiss human-in-the-loop gate before any correction message
goes out.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import require_analyst
from app.models import Narrative, NarrativeDelivery, Subscription
from app.modules.narratives.service import approve_narrative, detect_narratives, dismiss_narrative

router = APIRouter(tags=["narratives"])


def _narrative_out(n: Narrative) -> dict:
    return {
        "id": str(n.id),
        "hazard_type": n.hazard_type,
        "report_ids": n.report_ids,
        "report_count": n.report_count,
        "h3_cells": n.h3_cells,
        "representative_text": n.representative_text,
        "instrument_flat": n.instrument_flat,
        "rejected_report_count": n.rejected_report_count,
        "status": n.status,
        "message": n.message,
        "draft_method": n.draft_method,
        "detected_at": n.detected_at.isoformat(),
        "updated_at": n.updated_at.isoformat(),
        "reviewed_by": n.reviewed_by,
        "reviewed_at": n.reviewed_at.isoformat() if n.reviewed_at else None,
    }


@router.get("/analyst/narratives")
def list_narratives(
    limit: int = 50,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.scalars(select(Narrative).order_by(Narrative.detected_at.desc()).limit(min(limit, 200))).all()
    return [_narrative_out(n) for n in rows]


@router.post("/analyst/narratives/detect")
def detect_narratives_endpoint(
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    return {"flagged": detect_narratives(db)}


@router.post("/analyst/narratives/{narrative_id}/approve")
def approve_narrative_endpoint(
    narrative_id: uuid.UUID,
    analyst: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    narrative = db.get(Narrative, narrative_id)
    if narrative is None:
        raise HTTPException(status_code=404, detail="Narrative not found")
    try:
        n_delivered = approve_narrative(db, narrative, analyst=analyst)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    out = _narrative_out(narrative)
    out["delivered_count"] = n_delivered
    return out


@router.post("/analyst/narratives/{narrative_id}/dismiss")
def dismiss_narrative_endpoint(
    narrative_id: uuid.UUID,
    analyst: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    narrative = db.get(Narrative, narrative_id)
    if narrative is None:
        raise HTTPException(status_code=404, detail="Narrative not found")
    try:
        return _narrative_out(dismiss_narrative(db, narrative, analyst=analyst))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/analyst/narratives/{narrative_id}/deliveries")
def list_narrative_deliveries(
    narrative_id: uuid.UUID,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Delivery attempts for one approved correction — lets an analyst (or
    the drill) confirm the fan-out actually reached subscribers, same check
    `/analyst/alerts/{id}/deliveries` offers for hazard alerts."""
    rows = db.scalars(
        select(NarrativeDelivery)
        .where(NarrativeDelivery.narrative_id == narrative_id)
        .order_by(NarrativeDelivery.attempted_at.desc())
    ).all()
    out = []
    for d in rows:
        sub = db.get(Subscription, d.subscription_id)
        out.append(
            {
                "id": str(d.id),
                "subscription_id": str(d.subscription_id),
                "channel": sub.channel if sub else None,
                "address": sub.address if sub else None,
                "status": d.status,
                "detail": d.detail,
                "attempted_at": d.attempted_at.isoformat(),
            }
        )
    return out
