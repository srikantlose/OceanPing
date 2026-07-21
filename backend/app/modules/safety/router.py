""""Mark Safe" endpoints.

Submission is public (no analyst auth) for the same reason /reports and
/route are: someone telling responders they're safe — or that they aren't —
must never be gated behind a login. Reading the check-in list *is*
analyst-only, since it's personal-location data about identifiable people
rather than the aggregate hazard picture the public map shows.
"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import require_analyst
from app.models import SafetyCheckin
from app.modules.ingest.service import clamp_observed_at
from app.modules.safety.service import (
    CHECKIN_STATUSES,
    checkin_counts,
    create_checkin,
    find_by_client_key,
    recent_checkins,
)

router = APIRouter(tags=["safety"])


def _checkin_out(c: SafetyCheckin) -> dict:
    return {
        "id": str(c.id),
        "status": c.status,
        "lat": c.lat,
        "lon": c.lon,
        "h3_cell": c.h3_cell,
        "note": c.note,
        "observed_at": c.observed_at.isoformat(),
        "created_at": c.created_at.isoformat(),
    }


@router.post("/safety/checkin")
def submit_checkin(
    lat: Annotated[float, Form()],
    lon: Annotated[float, Form()],
    client_id: Annotated[str, Form()],
    status: Annotated[str, Form()],
    note: Annotated[str | None, Form()] = None,
    observed_at: Annotated[datetime | None, Form()] = None,
    client_key: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
) -> dict:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=422, detail="Invalid coordinates")
    if status not in CHECKIN_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {list(CHECKIN_STATUSES)}")

    existing = find_by_client_key(db, client_key)
    if existing is not None:
        return _checkin_out(existing)

    checkin = create_checkin(
        db,
        source="mobile",
        external_id=client_id,
        status=status,
        lat=lat,
        lon=lon,
        note=note,
        observed_at=clamp_observed_at(observed_at, get_settings().offline_max_report_age_hours),
        client_key=client_key,
    )
    return _checkin_out(checkin)


@router.get("/analyst/safety/checkins")
def list_checkins(
    hours: float = 48.0,
    status: str | None = None,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    if status is not None and status not in CHECKIN_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {list(CHECKIN_STATUSES)}")
    return [_checkin_out(c) for c in recent_checkins(db, hours=hours, status=status)]


@router.get("/analyst/safety/summary")
def safety_summary(
    hours: float = 48.0,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    return checkin_counts(db, hours=hours)
