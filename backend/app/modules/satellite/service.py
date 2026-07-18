"""Scheduled job: for each recent, still-open incident whose hazard type has
a satellite recipe, ask the configured provider for a scene observation and
record it. Mirrors sensors/service.py's poll-then-let-scoring-pick-it-up
shape. Satellite latency is hours, so this runs far less often than the
ERDDAP poll and never blocks or gates a rescore — scoring/service.py just
reads whatever observations exist (possibly none) on each rescore.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Incident, SatelliteObservation
from app.modules.satellite.providers import HAZARD_RECIPES, get_provider

log = logging.getLogger(__name__)


def poll_satellite(db: Session) -> int:
    settings = get_settings()
    since = datetime.now(timezone.utc) - timedelta(hours=settings.satellite_active_incident_hours)
    incidents = db.scalars(
        select(Incident)
        .where(Incident.last_seen >= since)
        .where(Incident.status != "rejected")
    ).all()

    provider = get_provider()
    inserted = 0
    for incident in incidents:
        recipe = HAZARD_RECIPES.get(incident.hazard_type)
        if not recipe:
            continue
        try:
            result = provider.observe(incident, recipe)
        except Exception:
            log.exception("Satellite provider %s failed for incident %s", provider.name, incident.id)
            continue
        if result is None:
            continue
        db.add(
            SatelliteObservation(
                incident_id=incident.id,
                provider=result.provider,
                recipe=result.recipe,
                score=result.score,
                scene_time=result.scene_time,
                scene_url=result.scene_url,
            )
        )
        inserted += 1
    db.commit()
    return inserted
