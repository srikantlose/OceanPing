"""DB-facing rumor detection, correction drafting, and delivery.

`detect_narratives()` is the scheduled job (and the drill/analyst on-demand
entry point): cluster recent reports by text-embedding similarity, and
persist a `Narrative` row only for a cluster that also contradicts something
real — an unremarkable cluster of true reports never becomes a "rumor" here.
Approving a narrative correction fans it out through the same Subscription-
geofence audience a hazard alert reaches (see modules/delivery/), but through
its own `NarrativeDelivery` log — never as a real `Alert` row, since an
approved correction isn't a hazard-tier proposal and must never be mistaken
for one by `sync_incident_alert`'s tier-upgrade logic (which would otherwise
find it via `_active_alert()` and could silently overwrite it later).
"""
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Narrative, NarrativeDelivery, Report, Subscription
from app.modules.chat import llm as llm_mod
from app.modules.delivery.adapters import get_adapter
from app.modules.forecast.service import nearest_pilot_location
from app.modules.ingest.report_conversation import HAZARD_LABELS_BY_LANG, SUPPORTED_LANGS
from app.modules.narratives import engine
from app.modules.scoring.audit import append_audit
from app.modules.scoring.engine import HAZARD_VARIABLES
from app.modules.scoring.service import instrument_zscores_near

log = logging.getLogger(__name__)

# An existing open narrative is reused (not duplicated) when a fresh cluster
# shares at least this fraction of its smaller report set — same "extend,
# don't fork" merge discipline nlp/dedup.py uses for incidents.
OVERLAP_THRESHOLD = 0.5

_LLM_POLISH_SYSTEM_PROMPT = (
    "You rewrite short public-safety correction messages so they read more "
    "naturally in English. Rewrite the message the user gives you, but you "
    "MUST NOT add, remove, or change any fact, number, place name, or hazard "
    "name in it. Reply with only the rewritten message text, nothing else."
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _candidate_reports(db: Session, since: datetime) -> list[dict]:
    reports = db.scalars(
        select(Report)
        .where(Report.created_at >= since)
        .where(Report.embedding.is_not(None))
        .where(Report.text.is_not(None))
    ).all()
    return [
        {
            "id": str(r.id),
            "h3_cell": r.h3_cell,
            "embedding": r.embedding,
            "text": r.text,
            "hazard_type": r.hazard_type,
            "lat": r.lat,
            "lon": r.lon,
            "status": r.status,
            "created_at": r.created_at,
        }
        for r in reports
    ]


def _overlaps(existing_ids: list[str], new_ids: list[str]) -> bool:
    if not existing_ids or not new_ids:
        return False
    overlap = len(set(existing_ids) & set(new_ids))
    return overlap / min(len(existing_ids), len(new_ids)) >= OVERLAP_THRESHOLD


def _matching_narrative(db: Session, hazard_type: str, report_ids: list[str]) -> Narrative | None:
    """Most recent narrative of this hazard whose report set substantially
    overlaps `report_ids`, whatever its status — the caller decides what to do
    based on that status (see detect_narratives)."""
    candidates = db.scalars(
        select(Narrative)
        .where(Narrative.hazard_type == hazard_type)
        .order_by(Narrative.detected_at.desc())
    ).all()
    return next((n for n in candidates if _overlaps(n.report_ids, report_ids)), None)


def _llm_polish(template_text: str) -> str | None:
    """Ask the LLM to smooth the English template's phrasing without
    changing any fact in it. Falls back to the raw template (draft_method
    stays "template") whenever no adapter is configured or the call fails —
    same credential-gated-degrade pattern as the RAG chatbot's own LLM call.
    Tamil/Telugu are never sent through this — see engine.py's module
    docstring for why."""
    return llm_mod.get_adapter().complete(_LLM_POLISH_SYSTEM_PROMPT, template_text)


def _draft_correction(narrative: Narrative) -> None:
    location = nearest_pilot_location(narrative.centroid_lat, narrative.centroid_lon)
    hazard_labels = {
        lang: HAZARD_LABELS_BY_LANG[lang].get(narrative.hazard_type, narrative.hazard_type)
        for lang in SUPPORTED_LANGS
    }
    template = engine.compose_correction_all_langs(
        hazard_labels, location, narrative.instrument_flat, narrative.rejected_report_count
    )
    polished_en = _llm_polish(template["en"]["standard"])
    if polished_en is not None:
        narrative.message = {**template, "en": {"standard": polished_en, "short": template["en"]["short"]}}
        narrative.draft_method = "llm"
    else:
        narrative.message = template
        narrative.draft_method = "template"


def _create_narrative(
    db: Session, cluster: engine.Cluster, hazard_type: str, lat: float, lon: float,
    instrument_flat: bool, rejected_count: int,
) -> Narrative:
    narrative = Narrative(
        hazard_type=hazard_type,
        report_ids=cluster.report_ids,
        report_count=cluster.report_count,
        h3_cells=sorted(cluster.h3_cells),
        centroid_lat=lat,
        centroid_lon=lon,
        representative_text=cluster.representative_text(),
        instrument_flat=instrument_flat,
        rejected_report_count=rejected_count,
        status="draft",
    )
    db.add(narrative)
    db.flush()
    _draft_correction(narrative)
    db.flush()
    append_audit(
        db, event_type="narrative.flagged", subject_type="narrative", subject_id=str(narrative.id),
        payload={
            "hazard_type": hazard_type, "report_count": narrative.report_count,
            "instrument_flat": instrument_flat, "rejected_report_count": rejected_count,
            "draft_method": narrative.draft_method,
        },
    )
    return narrative


def _update_narrative(
    db: Session, narrative: Narrative, cluster: engine.Cluster, lat: float, lon: float,
    instrument_flat: bool, rejected_count: int,
) -> None:
    """Refresh an already-flagged narrative's factual footprint as more
    reports join the same rumor. Never touches `message`/`status` once
    drafted — same "don't retroactively reword" discipline as an issued
    Alert's snapshot fields."""
    narrative.report_ids = cluster.report_ids
    narrative.report_count = cluster.report_count
    narrative.h3_cells = sorted(cluster.h3_cells)
    narrative.centroid_lat = lat
    narrative.centroid_lon = lon
    narrative.instrument_flat = instrument_flat
    narrative.rejected_report_count = rejected_count
    append_audit(
        db, event_type="narrative.updated", subject_type="narrative", subject_id=str(narrative.id),
        payload={"report_count": narrative.report_count, "rejected_report_count": rejected_count},
    )


def detect_narratives(db: Session, as_of: datetime | None = None) -> int:
    """Cluster recent reports by text-embedding similarity and flag/refresh
    narratives that contradict real state. Returns the number of newly
    flagged narratives (not counting refreshes of existing ones)."""
    settings = get_settings()
    reference = as_of or _utcnow()
    since = reference - timedelta(hours=settings.narrative_window_hours)
    candidates = _candidate_reports(db, since)
    clusters = engine.cluster_reports(candidates, sim_threshold=settings.narrative_sim_threshold)

    n_flagged = 0
    for cluster in clusters:
        hazard_type = cluster.dominant_hazard()
        lat, lon = cluster.centroid()
        rejected_count = cluster.rejected_count()
        has_signal = bool(HAZARD_VARIABLES.get(hazard_type))
        instrument_flat = has_signal and not instrument_zscores_near(db, hazard_type, lat, lon)
        if not engine.is_contradiction(instrument_flat, has_signal, rejected_count):
            continue

        existing = _matching_narrative(db, hazard_type, cluster.report_ids)
        if existing is not None and existing.status == "dismissed":
            # An analyst already looked at this claim and judged it real (or
            # not worth correcting). Re-flagging it every tick would just
            # spam the queue with a decision that's already been made.
            continue
        if existing is not None and existing.status == "draft":
            # Still pending review — fold the newly-joined reports into it
            # rather than queueing a second draft for the same rumor.
            _update_narrative(db, existing, cluster, lat, lon, instrument_flat, rejected_count)
            continue

        # Either nothing matches, or the last match was already approved and
        # its correction sent. A rumor resurging after a correction went out
        # is genuinely new information (the correction didn't land), and the
        # sent narrative is a finished artifact — same immutable-once-acted-on
        # discipline as a filed SITREP — so it gets its own fresh draft.
        _create_narrative(db, cluster, hazard_type, lat, lon, instrument_flat, rejected_count)
        n_flagged += 1
    db.commit()
    return n_flagged


def _matching_subscriptions(db: Session, narrative: Narrative) -> list[Subscription]:
    if not narrative.h3_cells:
        return []
    cells = set(narrative.h3_cells)
    subs = db.scalars(select(Subscription)).all()
    return [s for s in subs if set(s.h3_cells or []) & cells]


def deliver_narrative_correction(db: Session, narrative: Narrative) -> int:
    """Sends the approved correction to every subscriber geofenced over the
    narrative's footprint, regardless of their alert `min_tier` preference —
    a correction ("stand down, this wasn't real") is arguably even more
    universally relevant than a new hazard alert, not less, so tier gating
    doesn't apply here (a deliberate simplification from the tiered alert
    path, not an oversight).

    Reuses the real channel adapters (`delivery/adapters.get_adapter`)
    through a minimal duck-typed stand-in carrying just the three attributes
    they actually read (`.message`, `.tier`, `.hazard_type`) — not a real
    `Alert` row, so this can never be found by `alerts/service.py`'s
    tier-upgrade logic."""
    shim = SimpleNamespace(message=narrative.message, tier="correction", hazard_type=narrative.hazard_type)
    n = 0
    for sub in _matching_subscriptions(db, narrative):
        adapter = get_adapter(sub.channel)
        if adapter is None:
            continue
        try:
            result = adapter.send(shim, sub)
        except Exception as exc:
            status, detail = "failed", str(exc)[:500]
        else:
            status, detail = result.status, result.detail
        db.add(NarrativeDelivery(narrative_id=narrative.id, subscription_id=sub.id, status=status, detail=detail))
        n += 1
    db.commit()
    return n


def approve_narrative(db: Session, narrative: Narrative, analyst: str) -> int:
    """The only path a correction can go out — same human-in-the-loop
    discipline as `issue_warning()` for real hazard alerts. Returns the
    number of delivery attempts made."""
    if narrative.status != "draft":
        raise ValueError(f"Narrative already {narrative.status}")
    narrative.status = "approved"
    narrative.reviewed_by = analyst
    narrative.reviewed_at = _utcnow()
    append_audit(
        db, event_type="narrative.approved", subject_type="narrative", subject_id=str(narrative.id),
        payload={"analyst": analyst},
    )
    db.commit()
    return deliver_narrative_correction(db, narrative)


def dismiss_narrative(db: Session, narrative: Narrative, analyst: str) -> Narrative:
    if narrative.status != "draft":
        raise ValueError(f"Narrative already {narrative.status}")
    narrative.status = "dismissed"
    narrative.reviewed_by = analyst
    narrative.reviewed_at = _utcnow()
    append_audit(
        db, event_type="narrative.dismissed", subject_type="narrative", subject_id=str(narrative.id),
        payload={"analyst": analyst},
    )
    db.commit()
    return narrative
