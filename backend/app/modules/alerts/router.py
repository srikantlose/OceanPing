import hmac
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import require_analyst
from app.models import Alert, AlertDelivery, Incident, OfficialAdvisory, Subscription
from app.modules.alerts.cap import alert_to_cap_xml, alerts_feed_xml
from app.modules.alerts.cap_service import ingest_cap_document
from app.modules.alerts.engine import message_text
from app.modules.alerts.service import expire_alert, issue_warning
from app.modules.geo.h3utils import cell_polygon

log = logging.getLogger(__name__)

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


@router.get("/cap/alerts/{alert_id}.cap")
def cap_alert_document(alert_id: uuid.UUID, lang: str = "en", db: Session = Depends(get_db)) -> Response:
    """One alert as a real CAP 1.2 document — public, same trust boundary as
    /map/alerts, since a CAP document is meant for wide official/public
    redistribution by design."""
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    settings = get_settings()
    xml = alert_to_cap_xml(
        alert,
        sender=settings.cap_sender,
        sender_name=settings.cap_sender_name,
        base_url=settings.public_base_url,
        lang=lang,
        default_expiry_hours=settings.alert_default_expiry_hours,
    )
    return Response(content=xml, media_type="application/cap+xml")


@router.get("/cap/feed")
def cap_feed(db: Session = Depends(get_db)) -> Response:
    """Atom index of active alerts, each entry linking to its CAP document —
    the one URL a partner system polls to discover what's new."""
    settings = get_settings()
    alerts = db.scalars(select(Alert).where(Alert.status == "active").order_by(Alert.created_at.desc())).all()
    xml = alerts_feed_xml(alerts, base_url=settings.public_base_url)
    return Response(content=xml, media_type="application/atom+xml")


@router.post("/webhooks/cap")
async def cap_ingest_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    """Inbound CAP ingestion (phase 4, milestone 1) — a real NDMA/SACHET feed
    would push (or be polled for) documents here. Gated on a shared API key,
    same credential-checked-if-set, skipped-with-a-warning-if-not posture as
    whatsapp_app_secret's signature check (see whatsapp/client.py), since no
    real partner credential exists yet to require one."""
    settings = get_settings()
    if settings.cap_ingest_api_key:
        provided = request.headers.get("x-api-key", "")
        if not hmac.compare_digest(provided, settings.cap_ingest_api_key):
            raise HTTPException(status_code=403, detail="invalid api key")
    else:
        log.warning("cap_ingest_api_key not configured; skipping webhook auth")

    xml_text = (await request.body()).decode("utf-8")
    created = ingest_cap_document(db, xml_text)
    return {"status": "ok", "advisories_created": len(created)}


@router.get("/analyst/official-advisories")
def list_official_advisories(
    limit: int = 100,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Ingested official advisories — lets an analyst confirm an inbound CAP
    document actually landed and see what it's corroborating, mirroring
    /analyst/alerts/{id}/deliveries's role for the outbound side."""
    rows = db.scalars(
        select(OfficialAdvisory).order_by(OfficialAdvisory.ingested_at.desc()).limit(min(limit, 500))
    ).all()
    return [
        {
            "id": str(a.id),
            "cap_identifier": a.cap_identifier,
            "sender": a.sender,
            "event": a.event,
            "hazard_type": a.hazard_type,
            "urgency": a.urgency,
            "severity": a.severity,
            "certainty": a.certainty,
            "msg_type": a.msg_type,
            "effective_at": a.effective_at.isoformat() if a.effective_at else None,
            "expires_at": a.expires_at.isoformat() if a.expires_at else None,
            "ingested_at": a.ingested_at.isoformat(),
        }
        for a in rows
    ]


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
                    "message": message_text(a.message, "en", "push"),
                    "issued_by": a.issued_by or "automatic",
                    "predicted_flooded_cells_count": len(a.predicted_flooded_cells or []),
                    "projected_cells_count": len(a.projected_cells or []),
                    "created_at": a.created_at.isoformat(),
                    "expires_at": a.expires_at.isoformat() if a.expires_at else None,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}
