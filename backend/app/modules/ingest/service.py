"""Report creation pipeline — the single entry point used by the web API, the
Telegram bot, and the drill injector, so every channel gets identical treatment:
rate limits → reporter → NLP → H3 → media forensics → incident dedup → scoring."""
import hashlib
import logging
from datetime import datetime, timedelta, timezone

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


def clamp_observed_at(observed_at: datetime | None, max_age_hours: float, now: datetime | None = None) -> datetime:
    """Bound a client-supplied observation time to something a queued report
    could plausibly carry (phase 3, milestone 5).

    The mobile app's offline queue has to send when a sighting *happened*, not
    when the device finally found a network — otherwise a report held for
    hours lands stamped "now" and silently corroborates whatever is happening
    at sync time. But that timestamp is client-controlled on a public
    endpoint, and both the coherence signal (±30 min, scoring/service.py) and
    incident merge (incident_window_hours) key off it, so an unbounded value
    would let someone place a report inside any window they liked. Clamping
    rather than rejecting keeps a phone with a skewed clock usable: future
    times collapse to now, and anything older than the queue could plausibly
    have held collapses to the floor.
    """
    now = now or datetime.now(timezone.utc)
    if observed_at is None:
        return now
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    floor = now - timedelta(hours=max_age_hours)
    return max(floor, min(observed_at, now))


def find_by_client_key(db: Session, client_key: str | None) -> Report | None:
    """An already-accepted submission for this idempotency key, if any — see
    Report.client_key for why a retrying offline queue needs this."""
    if not client_key:
        return None
    return db.scalar(select(Report).where(Report.client_key == client_key))


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


def _attach_media(
    db: Session,
    report: Report,
    media_bytes: bytes,
    media_filename: str | None,
    lat: float,
    lon: float,
    created_at: datetime,
) -> None:
    """Forensics are cheap file I/O (a perceptual hash, EXIF parsing), not the
    NLP-pipeline's CPU-heavy embedding step, so this runs synchronously in the
    gateway in both pipeline modes — there's no reason to defer it, and the
    "media" confidence component being real from the first read the caller
    ever sees (rather than provisional) is only a plus."""
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


def audit_report_created(db: Session, report: Report, nlp_mode: str) -> None:
    """The report.created audit entry — fired right after incident assignment
    in both pipeline modes (inline: still inside create_report(); bus: from
    the dedup consumer, see consumers/dedup.py), since incident_id needs to
    be known first."""
    append_audit(
        db,
        event_type="report.created",
        subject_type="report",
        subject_id=str(report.id),
        payload={
            "source": report.source,
            "hazard_type": report.hazard_type,
            "h3_cell": report.h3_cell,
            "lang": report.lang,
            "nlp_mode": nlp_mode,
            "incident_id": str(report.incident_id),
        },
    )


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
    client_key: str | None = None,
) -> Report:
    """The single entry point for every channel. In the default "inline"
    pipeline mode this runs NLP, dedup, and scoring synchronously, exactly as
    every prior milestone built it. In "bus" mode (phase 3, milestone 8 — see
    modules/ingest/bus.py) this function becomes just the gateway: it still
    owns rate-limiting, the reporter, and media forensics, but classification,
    incident assignment, and scoring instead happen in three independent
    consumer processes reading a real Redpanda topic chain, and this function
    returns as soon as the report is durably queued for them."""
    created_at = created_at or datetime.now(timezone.utc)
    cell = cell_for(lat, lon)
    reporter = get_or_create_reporter(db, source, external_id)
    _check_rate_limits(reporter.external_id_hash, cell)

    explicit_hazard = hazard_type if hazard_type in HAZARD_TYPES else None  # user's explicit pick wins
    lang = classifier.detect_lang(text) if text else "und"
    urgency = classifier.detect_urgency(text)
    bus_mode = get_settings().pipeline_mode == "bus"

    if bus_mode:
        report = Report(
            reporter_id=reporter.id,
            lat=lat,
            lon=lon,
            geom=f"SRID=4326;POINT({lon} {lat})",
            h3_cell=cell,
            # Placeholder pending the nlp consumer, unless the reporter's own
            # pick is already final — real classification never overwrites it.
            hazard_type=explicit_hazard or "other",
            hazard_locked=explicit_hazard is not None,
            urgency=urgency,
            text=text,
            lang=lang,
            source=source,
            embedding=None,
            client_key=client_key,
            created_at=created_at,
            confidence_components={"media": engine.MEDIA_NEUTRAL},
            processing_stage="queued",
        )
    else:
        classification = classifier.classify(text)
        final_hazard = explicit_hazard or classification.hazard_type or "other"
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
            urgency=urgency,
            text=text,
            lang=lang,
            source=source,
            embedding=embedding,
            client_key=client_key,
            created_at=created_at,
            confidence_components={"media": engine.MEDIA_NEUTRAL},
        )
    db.add(report)
    db.flush()

    if media_bytes:
        _attach_media(db, report, media_bytes, media_filename, lat, lon, created_at)

    if bus_mode:
        db.commit()
        from app.modules.ingest import bus  # lazy: only bus mode needs a Kafka client

        bus.ensure_topics()
        bus.produce(bus.TOPIC_RAW, str(report.id))
        return report

    assign_incident(db, report)
    audit_report_created(db, report, classification.mode)
    rescore_report(db, report)
    db.commit()
    return report
