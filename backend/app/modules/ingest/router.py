from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.models import HAZARD_TYPES
from app.modules.ingest.schemas import ReportOut
from app.modules.ingest.service import (
    RateLimited,
    clamp_observed_at,
    create_report,
    find_by_client_key,
)

router = APIRouter(tags=["ingest"])


@router.get("/hazard-types")
def hazard_types() -> list[str]:
    return HAZARD_TYPES


@router.post("/reports", response_model=ReportOut)
async def submit_report(
    lat: Annotated[float, Form()],
    lon: Annotated[float, Form()],
    client_id: Annotated[str, Form()],
    hazard_type: Annotated[str | None, Form()] = None,
    text: Annotated[str | None, Form()] = None,
    # Offline-queue fields (phase 3, milestone 5). Both optional, so the web
    # form and every existing caller keep working unchanged.
    observed_at: Annotated[datetime | None, Form()] = None,
    client_key: Annotated[str | None, Form()] = None,
    photo: UploadFile | None = None,
    db: Session = Depends(get_db),
) -> ReportOut:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=422, detail="Invalid coordinates")
    if hazard_type is not None and hazard_type not in HAZARD_TYPES:
        raise HTTPException(status_code=422, detail=f"hazard_type must be one of {HAZARD_TYPES}")

    # Idempotent replay: a queued submission whose reply was lost gets the
    # original report back rather than creating a second one. Checked before
    # the rate limiter too, so a retry can't burn the caller's own quota.
    existing = find_by_client_key(db, client_key)
    if existing is not None:
        return ReportOut.from_report(existing)

    media_bytes = await photo.read() if photo is not None else None
    try:
        report = create_report(
            db,
            source="web",
            external_id=client_id,
            lat=lat,
            lon=lon,
            hazard_type=hazard_type,
            text=text,
            media_bytes=media_bytes,
            media_filename=photo.filename if photo else None,
            created_at=clamp_observed_at(observed_at, get_settings().offline_max_report_age_hours),
            client_key=client_key,
        )
    except RateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    return ReportOut.from_report(report)
