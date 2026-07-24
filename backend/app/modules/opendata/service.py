"""Open-data pipeline (phase 4, milestone 3): anonymized, H3-aggregated
event datasets for external researchers, gated by per-consumer API keys.

Three independently useful pieces:
  - aggregate_events() / build_dataset_release(): the DP + k-anonymity
    pipeline — turns raw verified reports into a released-shape row set,
    safe to publish, plus a frozen DatasetRelease snapshot of it.
  - anonymize_expired_reports(): the retention job — once a report is older
    than open_data_retention_months, its precise lat/lon/geom are
    permanently replaced by its H3 cell centroid (see geo/h3utils.py), the
    same fuzzing every public read path already applies at read time, now
    made a one-way write-time reduction in stored precision instead.
  - create_api_key() / verify_api_key() / revoke_api_key(): opaque bearer
    credentials for the gated download endpoint. Only a sha256 hash is ever
    stored — the same "never persist the credential itself" posture a
    password would get, even though these are machine credentials.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.redisclient import get_redis
from app.models import ApiKey, DatasetRelease, Report
from app.modules.geo.h3utils import cell_centroid, cell_to_parent
from app.modules.scoring.audit import append_audit

log = logging.getLogger(__name__)

API_KEY_PREFIX = "op_live_"


class RateLimited(Exception):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# API keys
# --------------------------------------------------------------------------

def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def create_api_key(db: Session, label: str, created_by: str) -> tuple[ApiKey, str]:
    """Mints a new key. Returns (row, raw_key) — raw_key exists only in this
    return value; only its hash is ever persisted, so a lost raw key means
    minting a new one, not a support ticket to "look it up"."""
    raw_key = API_KEY_PREFIX + secrets.token_urlsafe(24)
    row = ApiKey(
        label=label,
        key_hash=_hash_key(raw_key),
        key_prefix=raw_key[: len(API_KEY_PREFIX) + 6],
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    append_audit(
        db,
        event_type="opendata.api_key_created",
        subject_type="api_key",
        subject_id=str(row.id),
        payload={"label": label, "created_by": created_by, "key_prefix": row.key_prefix},
    )
    db.commit()
    return row, raw_key


def revoke_api_key(db: Session, key_id: uuid.UUID, revoked_by: str) -> ApiKey | None:
    row = db.get(ApiKey, key_id)
    if row is None or row.revoked_at is not None:
        return row
    row.revoked_at = _utcnow()
    append_audit(
        db,
        event_type="opendata.api_key_revoked",
        subject_type="api_key",
        subject_id=str(row.id),
        payload={"revoked_by": revoked_by},
    )
    db.commit()
    return row


def verify_api_key(db: Session, raw_key: str) -> ApiKey | None:
    """Active-key lookup for the request path. Returns None for both an
    unknown key and a revoked one — a caller can't distinguish the two,
    which is the point (a revoked key should fail exactly like it never
    existed)."""
    row = db.scalars(select(ApiKey).where(ApiKey.key_hash == _hash_key(raw_key))).first()
    if row is None or row.revoked_at is not None:
        return None
    row.last_used_at = _utcnow()
    db.commit()
    return row


def check_rate_limit(api_key_id: uuid.UUID) -> None:
    """Same real-Redis-INCR-plus-EXPIRE pattern as
    ingest/service.py::_check_rate_limits, scoped per API key instead of per
    reporter/cell. Fails open (logs and skips) if Redis is unreachable,
    matching that module's posture: a public research API being briefly
    unthrottled is a much smaller risk than it being unusable during a
    Redis blip."""
    settings = get_settings()
    key = f"rl:apikey:{api_key_id}"
    try:
        r = get_redis()
        count = r.incr(key)
        if count == 1:
            r.expire(key, 3600)
        if count > settings.open_data_rate_limit_per_hour:
            raise RateLimited("Rate limit exceeded for this API key; try again in a while.")
    except RateLimited:
        raise
    except Exception:
        log.warning("Redis unavailable; open-data rate limiting skipped", exc_info=True)


# --------------------------------------------------------------------------
# DP / k-anonymity aggregation
# --------------------------------------------------------------------------

def _laplace_noise(epsilon: float, rng: np.random.Generator) -> float:
    # Sensitivity 1: one added/removed report changes any single group's
    # count by at most 1, the standard Laplace-mechanism scale of 1/epsilon.
    return float(rng.laplace(loc=0.0, scale=1.0 / epsilon))


def aggregate_events(
    db: Session,
    period_start: datetime,
    period_end: datetime,
    *,
    h3_resolution: int | None = None,
    k_anonymity_min: int | None = None,
    dp_epsilon: float | None = None,
    rng: np.random.Generator | None = None,
) -> dict:
    """Groups verified reports in [period_start, period_end) by (coarsened
    H3 cell, hazard_type, day). A group with fewer than k_anonymity_min raw
    reports is dropped entirely — not noised — since Laplace noise can't
    retroactively hide that a true count of 1 (or 2, ...) was ever computed;
    k-anonymity has to be enforced before noise, not instead of it. Every
    surviving group's count then gets independent Laplace-mechanism DP
    noise. Returns the released rows plus the parameters/stats a
    DatasetRelease needs to record alongside them."""
    settings = get_settings()
    h3_resolution = h3_resolution if h3_resolution is not None else settings.open_data_h3_resolution
    k_anonymity_min = (
        k_anonymity_min if k_anonymity_min is not None else settings.open_data_k_anonymity_min
    )
    dp_epsilon = dp_epsilon if dp_epsilon is not None else settings.open_data_dp_epsilon
    rng = rng if rng is not None else np.random.default_rng()

    rows = db.execute(
        select(Report.h3_cell, Report.hazard_type, Report.created_at)
        .where(Report.status == "verified")
        .where(Report.created_at >= period_start)
        .where(Report.created_at < period_end)
    ).all()

    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for h3_cell, hazard_type, created_at in rows:
        parent_cell = cell_to_parent(h3_cell, h3_resolution)
        day = created_at.date().isoformat()
        counts[(parent_cell, hazard_type, day)] += 1

    released = []
    suppressed_group_count = 0
    for (parent_cell, hazard_type, day), true_count in sorted(counts.items()):
        if true_count < k_anonymity_min:
            suppressed_group_count += 1
            continue
        noised = int(round(true_count + _laplace_noise(dp_epsilon, rng)))
        noised = max(noised, 0)
        lat, lon = cell_centroid(parent_cell)
        released.append(
            {
                "h3_cell": parent_cell,
                "centroid_lat": round(lat, 4),
                "centroid_lon": round(lon, 4),
                "hazard_type": hazard_type,
                "date": day,
                "report_count": noised,
            }
        )

    return {
        "rows": released,
        "h3_resolution": h3_resolution,
        "k_anonymity_min": k_anonymity_min,
        "dp_epsilon": dp_epsilon,
        "row_count": len(released),
        "suppressed_group_count": suppressed_group_count,
    }


def build_dataset_release(
    db: Session, period_start: datetime, period_end: datetime, created_by: str
) -> DatasetRelease:
    """Runs aggregate_events() and freezes the result as a citable
    DatasetRelease — the checksum lets a researcher verify their copy still
    matches what was actually released."""
    agg = aggregate_events(db, period_start, period_end)
    checksum = hashlib.sha256(
        json.dumps(agg["rows"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    release = DatasetRelease(
        period_start=period_start,
        period_end=period_end,
        h3_resolution=agg["h3_resolution"],
        k_anonymity_min=agg["k_anonymity_min"],
        dp_epsilon=agg["dp_epsilon"],
        row_count=agg["row_count"],
        suppressed_group_count=agg["suppressed_group_count"],
        content=agg["rows"],
        checksum=checksum,
        created_by=created_by,
    )
    db.add(release)
    db.flush()
    append_audit(
        db,
        event_type="opendata.dataset_released",
        subject_type="dataset_release",
        subject_id=str(release.id),
        payload={
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "row_count": agg["row_count"],
            "suppressed_group_count": agg["suppressed_group_count"],
            "checksum": checksum,
        },
    )
    db.commit()
    return release


# --------------------------------------------------------------------------
# Retention / anonymization job
# --------------------------------------------------------------------------

def anonymize_expired_reports(db: Session) -> int:
    """Privacy retention (phase 4, milestone 3's "auto-anonymize exact
    locations after N months"): a real scheduled job (see core/scheduler.py),
    not just a documented policy — same posture as
    recovery/service.py::purge_expired_missing_persons. Unlike that job this
    one never deletes a Report (hazard/incident history stays intact for
    scoring and analytics); it only ever overwrites lat/lon/geom with the
    report's own H3 cell centroid, a one-way precision reduction from exact
    GPS to ~0.7 km^2 (h3_cell itself, and every derived table, is
    untouched)."""
    settings = get_settings()
    cutoff = _utcnow() - timedelta(days=settings.open_data_retention_months * 30.44)
    candidates = db.scalars(
        select(Report)
        .where(Report.created_at < cutoff)
        .where(Report.location_anonymized_at.is_(None))
    ).all()
    for report in candidates:
        lat, lon = cell_centroid(report.h3_cell)
        report.lat = lat
        report.lon = lon
        report.geom = f"SRID=4326;POINT({lon} {lat})"
        report.location_anonymized_at = _utcnow()
    if candidates:
        append_audit(
            db,
            event_type="opendata.locations_anonymized",
            subject_type="report",
            subject_id="batch",
            payload={"count": len(candidates), "retention_months": settings.open_data_retention_months},
        )
    db.commit()
    return len(candidates)
