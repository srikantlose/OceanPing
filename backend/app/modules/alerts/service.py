"""Alert lifecycle: automatic advisory/watch proposals from incident state,
analyst-only warnings, expiry, and Telegram broadcast to geofenced subscribers.

Milestone-1 scope: broadcast runs synchronously (in-process, best-effort) —
phase 1's later milestone moves this to a queued worker so delivery never
blocks report ingestion under real load.
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Alert, AlertDelivery, Incident, Subscription
from app.modules.alerts import engine
from app.modules.scoring.audit import append_audit

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _incident_signals(incident: Incident) -> tuple[int, float]:
    """(distinct reporter count, strongest instrument component) across the
    incident's merged reports — the inputs `engine.eligible_tier` needs."""
    reports = incident.reports
    n_reporters = len({r.reporter_id for r in reports})
    max_instrument = max(
        (r.confidence_components.get("instrument", 0.0) for r in reports), default=0.0
    )
    return n_reporters, max_instrument


def _active_alert(db: Session, incident_id) -> Alert | None:
    return db.scalars(
        select(Alert)
        .where(Alert.incident_id == incident_id)
        .where(Alert.status == "active")
        .order_by(Alert.created_at.desc())
        .limit(1)
    ).first()


def sync_incident_alert(db: Session, incident: Incident) -> Alert | None:
    """Auto-propose/upgrade an advisory or watch alert from current incident
    state. Never touches an analyst-issued warning, and never downgrades."""
    settings = get_settings()
    n_reporters, max_instrument = _incident_signals(incident)
    tier = engine.eligible_tier(
        incident.status, n_reporters, max_instrument, settings.alert_min_watch_reporters
    )
    if tier is None:
        return None

    active = _active_alert(db, incident.id)
    if active is not None and active.issued_by is not None:
        return active  # an analyst warning is never auto-modified
    if active is not None and engine.TIER_RANK[tier] <= engine.TIER_RANK[active.tier]:
        return active  # no upgrade needed

    message = engine.draft_message(incident.hazard_type, tier, incident.report_count)
    if active is not None:
        old_tier = active.tier
        active.tier = tier
        active.message = message
        active.h3_cells = incident.h3_cells
        alert = active
        event_type, payload = "alert.tier_changed", {"from": old_tier, "to": tier, "auto": True}
    else:
        alert = Alert(
            incident_id=incident.id,
            hazard_type=incident.hazard_type,
            tier=tier,
            h3_cells=incident.h3_cells,
            message=message,
            status="active",
            issued_by=None,
            created_at=_utcnow(),
        )
        db.add(alert)
        db.flush()
        event_type, payload = "alert.issued", {"tier": tier, "auto": True}

    append_audit(db, event_type=event_type, subject_type="alert", subject_id=str(alert.id), payload=payload)
    db.flush()
    _broadcast(db, alert)
    return alert


def issue_warning(
    db: Session, incident: Incident, analyst: str, note: str | None = None, expires_hours: float | None = None
) -> Alert:
    """The only path to the warning tier — always analyst-attributed and
    audit-logged. Reuses an incident's active alert row if one exists."""
    settings = get_settings()
    expires_at = _utcnow() + timedelta(hours=expires_hours or settings.alert_default_expiry_hours)
    message = engine.draft_message(incident.hazard_type, "warning", incident.report_count, note)

    active = _active_alert(db, incident.id)
    if active is not None:
        old_tier = active.tier
        active.tier = "warning"
        active.issued_by = analyst
        active.note = note
        active.message = message
        active.h3_cells = incident.h3_cells
        active.expires_at = expires_at
        alert = active
        event_type, payload = "alert.tier_changed", {"from": old_tier, "to": "warning", "analyst": analyst}
    else:
        alert = Alert(
            incident_id=incident.id,
            hazard_type=incident.hazard_type,
            tier="warning",
            h3_cells=incident.h3_cells,
            message=message,
            status="active",
            issued_by=analyst,
            note=note,
            created_at=_utcnow(),
            expires_at=expires_at,
        )
        db.add(alert)
        db.flush()
        event_type, payload = "alert.issued", {"tier": "warning", "analyst": analyst}

    append_audit(db, event_type=event_type, subject_type="alert", subject_id=str(alert.id), payload=payload)
    db.commit()
    _broadcast(db, alert)
    return alert


def expire_alert(db: Session, alert: Alert, analyst: str) -> Alert:
    alert.status = "expired"
    append_audit(
        db, event_type="alert.expired", subject_type="alert", subject_id=str(alert.id),
        payload={"analyst": analyst},
    )
    db.commit()
    return alert


def _broadcast(db: Session, alert: Alert) -> None:
    """Best-effort Telegram push to subscribers whose geofence intersects the
    alert's cells and whose min_tier is met. Failures never raise — a
    delivery outage must not block scoring/ingest."""
    token = get_settings().telegram_bot_token
    if not token:
        return
    alert_cells = set(alert.h3_cells or [])
    if not alert_cells:
        return
    subs = db.scalars(select(Subscription).where(Subscription.channel == "telegram")).all()
    for sub in subs:
        if engine.TIER_RANK.get(sub.min_tier, 0) > engine.TIER_RANK[alert.tier]:
            continue
        if not (set(sub.h3_cells or []) & alert_cells):
            continue
        text = alert.message.get(sub.lang) or alert.message.get("en", "")
        status, detail = "sent", None
        try:
            resp = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": sub.address, "text": text},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as exc:
            status, detail = "failed", str(exc)[:500]
            log.warning("Alert delivery failed for subscription %s: %s", sub.id, exc)
        db.add(
            AlertDelivery(
                alert_id=alert.id, subscription_id=sub.id, status=status, detail=detail,
                attempted_at=_utcnow(),
            )
        )
    db.commit()
