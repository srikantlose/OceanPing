"""Open-data API (phase 4, milestone 3): a public, unauthenticated dataset
catalog (a researcher needs to see what exists before requesting a key) plus
an API-key-gated, rate-limited download endpoint for the actual rows — and
the analyst-only admin surface for minting keys and building releases.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import require_analyst
from app.models import ApiKey, DatasetRelease
from app.modules.opendata.service import (
    RateLimited,
    build_dataset_release,
    check_rate_limit,
    create_api_key,
    revoke_api_key,
    verify_api_key,
)

router = APIRouter(tags=["opendata"])

_bearer = HTTPBearer(auto_error=False)


def require_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> ApiKey:
    """Same Bearer-token shape as core/security.py::require_analyst, but
    verified against a hashed, per-consumer API key instead of a signed
    session token."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing API key")
    key = verify_api_key(db, credentials.credentials)
    if key is None:
        raise HTTPException(status_code=403, detail="Invalid or revoked API key")
    try:
        check_rate_limit(key.id)
    except RateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    return key


def _release_summary(r: DatasetRelease) -> dict:
    return {
        "id": str(r.id),
        "period_start": r.period_start.isoformat(),
        "period_end": r.period_end.isoformat(),
        "h3_resolution": r.h3_resolution,
        "k_anonymity_min": r.k_anonymity_min,
        "dp_epsilon": r.dp_epsilon,
        "row_count": r.row_count,
        "suppressed_group_count": r.suppressed_group_count,
        "checksum": r.checksum,
        "doi": r.doi,
        "created_at": r.created_at.isoformat(),
    }


@router.get("/opendata/datasets")
def list_datasets(db: Session = Depends(get_db)) -> list[dict]:
    releases = db.scalars(select(DatasetRelease).order_by(DatasetRelease.created_at.desc())).all()
    return [_release_summary(r) for r in releases]


@router.get("/opendata/datasets/{release_id}")
def download_dataset(
    release_id: uuid.UUID,
    _: ApiKey = Depends(require_api_key),
    db: Session = Depends(get_db),
) -> dict:
    release = db.get(DatasetRelease, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Dataset release not found")
    return {**_release_summary(release), "rows": release.content}


class ApiKeyIn(BaseModel):
    label: str


@router.post("/analyst/opendata/api-keys")
def mint_api_key(
    body: ApiKeyIn,
    analyst: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    row, raw_key = create_api_key(db, label=body.label, created_by=analyst)
    # raw_key is returned exactly once, at creation — only its hash is
    # ever stored, so this response is the sole chance to record it.
    return {"id": str(row.id), "label": row.label, "key": raw_key, "key_prefix": row.key_prefix}


@router.get("/analyst/opendata/api-keys")
def list_api_keys(
    analyst: str = Depends(require_analyst), db: Session = Depends(get_db)
) -> list[dict]:
    keys = db.scalars(select(ApiKey).order_by(ApiKey.created_at.desc())).all()
    return [
        {
            "id": str(k.id),
            "label": k.label,
            "key_prefix": k.key_prefix,
            "created_by": k.created_by,
            "created_at": k.created_at.isoformat(),
            "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        }
        for k in keys
    ]


@router.post("/analyst/opendata/api-keys/{key_id}/revoke")
def revoke_key(
    key_id: uuid.UUID, analyst: str = Depends(require_analyst), db: Session = Depends(get_db)
) -> dict:
    row = revoke_api_key(db, key_id, revoked_by=analyst)
    if row is None:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"id": str(row.id), "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None}


class ReleaseIn(BaseModel):
    period_start: datetime
    period_end: datetime


def _ensure_utc(dt: datetime) -> datetime:
    """A period bound with no offset is assumed UTC rather than rejected —
    same defensive posture as cap_ingest.py::_parse_dt for externally
    supplied datetimes, needed here since Report.created_at is tz-aware."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.post("/analyst/opendata/releases")
def create_release(
    body: ReleaseIn,
    analyst: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    period_start = _ensure_utc(body.period_start)
    period_end = _ensure_utc(body.period_end)
    if period_end <= period_start:
        raise HTTPException(status_code=422, detail="period_end must be after period_start")
    release = build_dataset_release(db, period_start, period_end, created_by=analyst)
    return _release_summary(release)
