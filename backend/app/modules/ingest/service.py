"""Report creation pipeline — the single entry point used by the web API, the
Telegram bot, and the drill injector, so every channel gets identical treatment:
rate limits → reporter → NLP → H3 → media forensics → incident dedup → scoring."""
import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.redisclient import get_redis
from app.models import HAZARD_TYPES, MediaAsset, Report, Reporter
from app.modules.geo.h3utils import cell_for
from app.modules.ingest import media as media_mod
from app.modules.nlp import classifier
from app.modules.nlp.dedup import assign_incident
from app.modules.scoring import engine
from app.modules.scoring.audit import append_audit
from app.modules.scoring.service import rescore_report

log = logging.getLogger(__name__)


class RateLimited(Exception):
    pass


# Fisherman mode (phase 2, milestone 5): a role registered via cooperative
# membership (see modules/fisherman/roster.py) starts with more trust than an
# anonymous citizen account, since cooperative membership is itself a real
# identity check this app can't otherwise perform.
FISHERMAN_START_TRUST = 0.65


def _hash_identity(source: str, external_id: str) -> str:
    return hashlib.sha256(f"{source}:{external_id}".encode()).hexdigest()


def _check_rate_limits(reporter_hash: str, cell: str) -> None:
    settings = get_settings()
    try:
        r = get_redis()
        pipe = r.pipeline()
        for key in (f"rl:rep:{reporter_hash}", f"rl:cell:{cell}"):
            pipe.incr(key)
            pipe.expire(key, 600)
        counts = pipe.execute()
        if counts[0] > settings.rate_limit_reports_per_reporter:
            raise RateLimited("Too many reports from this account; please wait a few minutes.")
        if counts[2] > settings.rate_limit_reports_per_cell:
            raise RateLimited("This area is receiving a very high volume of reports.")
    except RateLimited:
        raise
    except Exception:
        log.warning("Redis unavailable; rate limiting skipped", exc_info=True)


def get_or_create_reporter(db: Session, source: str, external_id: str, role: str = "citizen") -> Reporter:
    identity = _hash_identity(source, external_id)
    reporter = db.scalar(select(Reporter).where(Reporter.external_id_hash == identity))
    if reporter is None:
        reporter = Reporter(source=source, external_id_hash=identity, role=role)
        if role == "fisherman":
            reporter.trust_score = FISHERMAN_START_TRUST
        db.add(reporter)
        db.flush()
    return reporter


def create_report(
    db: Session,
    *,
    source: str,
    external_id: str,
    lat: float,
    lon: float,
    hazard_type: str | None = None,
    text: str | None = None,
    media_bytes: bytes | None = None,
    media_filename: str | None = None,
    created_at: datetime | None = None,
) -> Report:
    created_at = created_at or datetime.now(timezone.utc)
    cell = cell_for(lat, lon)
    reporter = get_or_create_reporter(db, source, external_id)
    _check_rate_limits(reporter.external_id_hash, cell)

    lang = classifier.detect_lang(text) if text else "und"
    classification = classifier.classify(text)
    if hazard_type in HAZARD_TYPES:
        final_hazard = hazard_type  # user's explicit pick wins
    else:
        final_hazard = classification.hazard_type or "other"
    embedding = classification.embedding
    if embedding is None and text:
        embedding = classifier.embed(text)

    report = Report(
        reporter_id=reporter.id,
        lat=lat,
        lon=lon,
        geom=f"SRID=4326;POINT({lon} {lat})",
        h3_cell=cell,
        hazard_type=final_hazard,
        urgency=classifier.detect_urgency(text),
        text=text,
        lang=lang,
        source=source,
        embedding=embedding,
        created_at=created_at,
        confidence_components={"media": engine.MEDIA_NEUTRAL},
    )
    db.add(report)
    db.flush()

    if media_bytes:
        path = media_mod.save_upload(media_bytes, media_filename)
        forensics = media_mod.run_forensics(db, path, lat, lon, created_at)
        db.add(
            MediaAsset(
                report_id=report.id,
                path=str(path),
                phash=forensics["phash"],
                exif={**forensics["exif"], "gps_km": forensics["gps_km"],
                      "time_offset_hours": forensics["time_offset_hours"],
                      "reused": forensics["reused"]},
            )
        )
        report.confidence_components = {
            "media": engine.media_score(
                has_media=True,
                phash_reused=forensics["reused"],
                exif_gps_km=forensics["gps_km"],
                exif_time_offset_hours=forensics["time_offset_hours"],
            ),
            "media_forensics": {
                "reused": forensics["reused"],
                "gps_km": forensics["gps_km"],
                "time_offset_hours": forensics["time_offset_hours"],
            },
        }

    assign_incident(db, report)
    append_audit(
        db,
        event_type="report.created",
        subject_type="report",
        subject_id=str(report.id),
        payload={
            "source": source,
            "hazard_type": final_hazard,
            "h3_cell": cell,
            "lang": lang,
            "nlp_mode": classification.mode,
            "incident_id": str(report.incident_id),
        },
    )
    rescore_report(db, report)
    db.commit()
    return report
