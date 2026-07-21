import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import require_analyst
from app.models import Alert, AlertDelivery, Incident, Subscription
from app.modules.alerts.service import expire_alert, issue_warning
from app.modules.geo.h3utils import cell_polygon

router = APIRouter(tags=["alerts"])


def _alert_out(alert: Alert) -> dict:
    return {
        "id": str(alert.id),
        "incident_id": str(alert.incident_id),
        "hazard_type": alert.hazard_type,
        "tier": alert.tier,
        "status": alert.status,
        "message": alert.message,
        "issued_by": alert.issued_by,
        "note": alert.note,
        "predicted_flooded_cells": alert.predicted_flooded_cells,
        "projected_cells": alert.projected_cells,
        "created_at": alert.created_at.isoformat(),
        "expires_at": alert.expires_at.isoformat() if alert.expires_at else None,
    }


class WarningIn(BaseModel):
    note: str | None = None
    expires_hours: float | None = None


@router.post("/analyst/incidents/{incident_id}/warning")
def issue_warning_endpoint(
    incident_id: uuid.UUID,
    body: WarningIn,
    analyst: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    alert = issue_warning(db, incident, analyst=analyst, note=body.note, expires_hours=body.expires_hours)
    return _alert_out(alert)


@router.post("/analyst/alerts/{alert_id}/expire")
def expire_alert_endpoint(
    alert_id: uuid.UUID,
    analyst: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.status != "active":
        raise HTTPException(status_code=409, detail=f"Alert already {alert.status}")
    return _alert_out(expire_alert(db, alert, analyst=analyst))


@router.get("/analyst/alerts")
def list_alerts(
    limit: int = 100,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    alerts = db.scalars(select(Alert).order_by(Alert.created_at.desc()).limit(min(limit, 500))).all()
    return [_alert_out(a) for a in alerts]


@router.get("/analyst/alerts/{alert_id}/deliveries")
def list_alert_deliveries(
    alert_id: uuid.UUID,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Delivery attempts for one alert — lets an analyst (or the drill) confirm
    the queued worker actually fanned it out, not just that it was issued."""
    rows = db.scalars(
        select(AlertDelivery).where(AlertDelivery.alert_id == alert_id).order_by(AlertDelivery.attempted_at.desc())
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


@router.get("/map/alerts")
def public_alerts(db: Session = Depends(get_db)) -> dict:
    alerts = db.scalars(select(Alert).where(Alert.status == "active")).all()
    features = []
    for a in alerts:
        cells = a.h3_cells or []
        if not cells:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [[cell_polygon(c)] for c in cells],
                },
                "properties": {
                    "id": str(a.id),
                    "tier": a.tier,
                    "hazard_type": a.hazard_type,
                    "message": a.message.get("en", ""),
                    "issued_by": a.issued_by or "automatic",
                    "predicted_flooded_cells_count": len(a.predicted_flooded_cells or []),
                    "projected_cells_count": len(a.projected_cells or []),
                    "created_at": a.created_at.isoformat(),
                    "expires_at": a.expires_at.isoformat() if a.expires_at else None,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}
