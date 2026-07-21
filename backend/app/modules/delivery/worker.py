"""Delivery worker: drains the alert queue and fans out to matching
subscriptions via channel adapters. Run as its own process/container:
    python -m app.modules.delivery.worker

Runs outside the API process so a slow or down channel provider can never
block report ingestion, scoring, or alert issuance.
"""
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models import Alert, AlertDelivery, Subscription
from app.modules.alerts import engine
from app.modules.delivery.adapters import get_adapter
from app.modules.delivery.queue import dequeue_alert

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# The issuing request enqueues an alert id before its own transaction commits
# (batch callers like rescore_recent() commit once at the end, after every
# report — and thus every sync_incident_alert() — has run). A worker on its
# own connection can pop the id first and miss the row under a plain lookup;
# a few short retries ride out that read-after-write gap instead of dropping
# the delivery.
_LOOKUP_RETRIES = 5
_LOOKUP_RETRY_DELAY_SECONDS = 0.2


def _matches(alert: Alert, sub: Subscription) -> bool:
    """Pure predicate: does this subscription want this alert? Split out from
    the DB query below so it's unit-testable without a live database.

    Geofence matching covers both the confirmed incident area (h3_cells) and
    the hazard-front propagation forecast's projected cells (phase 3,
    milestone 3) — a subscriber directly ahead of a moving front gets the
    same advisory/watch alert before they've reported anything themselves.
    projected_cells is purely additive here; it never affects routing
    exclusion or the confirmed-incident map layer (see models.py's comment
    on Alert.projected_cells)."""
    if engine.TIER_RANK.get(sub.min_tier, 0) > engine.TIER_RANK[alert.tier]:
        return False
    alert_cells = set(alert.h3_cells or []) | set(alert.projected_cells or [])
    return bool(set(sub.h3_cells or []) & alert_cells)


def _matching_subscriptions(db: Session, alert: Alert) -> list[Subscription]:
    if not alert.h3_cells and not alert.projected_cells:
        return []
    subs = db.scalars(select(Subscription)).all()
    return [s for s in subs if _matches(alert, s)]


def _load_alert_with_retries(db: Session, alert_id: str) -> Alert | None:
    for attempt in range(_LOOKUP_RETRIES):
        alert = db.get(Alert, alert_id)
        if alert is not None:
            return alert
        if attempt < _LOOKUP_RETRIES - 1:
            time.sleep(_LOOKUP_RETRY_DELAY_SECONDS)
    return None


def deliver_alert(db: Session, alert_id: str) -> int:
    """Fan out one alert to its matching subscriptions. Returns attempt count."""
    alert = _load_alert_with_retries(db, alert_id)
    if alert is None:
        log.warning("Queued alert %s never became visible — issuing transaction may have rolled back", alert_id)
        return 0
    n = 0
    for sub in _matching_subscriptions(db, alert):
        adapter = get_adapter(sub.channel)
        if adapter is None:
            continue
        try:
            result = adapter.send(alert, sub)
        except Exception as exc:
            status, detail = "failed", str(exc)[:500]
        else:
            status, detail = result.status, result.detail
        db.add(
            AlertDelivery(alert_id=alert.id, subscription_id=sub.id, status=status, detail=detail)
        )
        n += 1
    db.commit()
    return n


def main() -> None:
    log.info("OceanPing delivery worker started, draining queue…")
    while True:
        alert_id = dequeue_alert()
        if alert_id is None:
            continue
        db = SessionLocal()
        try:
            n = deliver_alert(db, alert_id)
            if n:
                log.info("Delivered alert %s to %d subscription(s)", alert_id, n)
        except Exception:
            log.exception("Delivery failed for alert %s", alert_id)
        finally:
            db.close()


if __name__ == "__main__":
    main()
