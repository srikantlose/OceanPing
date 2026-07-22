"""Recovery module API (phase 3, milestone 7): damage assessment, the
mutual-aid board, and the missing/found-person registry.

Submission is public for every one of these — same reasoning as /reports and
/safety/checkin: someone reporting damage, asking for aid, offering aid, or
telling responders someone is missing must never sit behind a login. Reading
back damage assessments and the aid board is analyst-only for now (nothing
here is public *yet* beyond /map/damage, see geo/router.py); reading the
missing-person registry is analyst-only *by design*, not just by omission —
it holds a name, description, and often a photo of an identifiable, often
vulnerable person (see models.py::MissingPerson)."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import require_analyst
from app.models import AidOffer, DamageAssessment, MissingPerson, ReliefRequest, RELIEF_CATEGORIES, MISSING_PERSON_TYPES
from app.modules.recovery import service

router = APIRouter(tags=["recovery"])


def _damage_out(a: DamageAssessment) -> dict:
    return {
        "id": str(a.id),
        "lat": a.lat,
        "lon": a.lon,
        "h3_cell": a.h3_cell,
        "damage_class": a.damage_class,
        "severity": a.severity,
        "cv_confidence": a.cv_confidence,
        "cv_mode": a.cv_mode,
        "cv_detail": a.cv_detail,
        "status": a.status,
        "note": a.note,
        "created_at": a.created_at.isoformat(),
    }


def _relief_out(r: ReliefRequest) -> dict:
    return {
        "id": str(r.id),
        "lat": r.lat,
        "lon": r.lon,
        "h3_cell": r.h3_cell,
        "category": r.category,
        "description": r.description,
        "people_count": r.people_count,
        "status": r.status,
        "fulfilled_by": r.fulfilled_by,
        "fulfilled_at": r.fulfilled_at.isoformat() if r.fulfilled_at else None,
        "created_at": r.created_at.isoformat(),
    }


def _offer_out(o: AidOffer) -> dict:
    return {
        "id": str(o.id),
        "lat": o.lat,
        "lon": o.lon,
        "h3_cell": o.h3_cell,
        "category": o.category,
        "description": o.description,
        "capacity": o.capacity,
        "status": o.status,
        "created_at": o.created_at.isoformat(),
    }


def _missing_out(p: MissingPerson) -> dict:
    return {
        "id": str(p.id),
        "report_type": p.report_type,
        "name": p.name,
        "age": p.age,
        "gender": p.gender,
        "description": p.description,
        "lat": p.lat,
        "lon": p.lon,
        "h3_cell": p.h3_cell,
        "has_photo": p.photo_path is not None,
        "status": p.status,
        "matched_person_id": str(p.matched_person_id) if p.matched_person_id else None,
        "resolved_by": p.resolved_by,
        "resolved_at": p.resolved_at.isoformat() if p.resolved_at else None,
        "created_at": p.created_at.isoformat(),
    }


# --- public submission --------------------------------------------------


@router.post("/recovery/damage")
async def submit_damage(
    lat: Annotated[float, Form()],
    lon: Annotated[float, Form()],
    client_id: Annotated[str, Form()],
    photo: UploadFile,
    note: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
) -> dict:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=422, detail="Invalid coordinates")
    media_bytes = await photo.read()
    if not media_bytes:
        raise HTTPException(status_code=422, detail="photo is required")
    assessment = service.submit_damage_assessment(
        db,
        source="web",
        external_id=client_id,
        lat=lat,
        lon=lon,
        media_bytes=media_bytes,
        media_filename=photo.filename,
        note=note,
    )
    return _damage_out(assessment)


@router.post("/recovery/relief-requests")
def submit_relief_request(
    lat: Annotated[float, Form()],
    lon: Annotated[float, Form()],
    client_id: Annotated[str, Form()],
    category: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
    people_count: Annotated[int | None, Form()] = None,
    db: Session = Depends(get_db),
) -> dict:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=422, detail="Invalid coordinates")
    try:
        req = service.create_relief_request(
            db, source="web", external_id=client_id, lat=lat, lon=lon,
            category=category, description=description, people_count=people_count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _relief_out(req)


@router.post("/recovery/aid-offers")
def submit_aid_offer(
    lat: Annotated[float, Form()],
    lon: Annotated[float, Form()],
    client_id: Annotated[str, Form()],
    category: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
    capacity: Annotated[int | None, Form()] = None,
    db: Session = Depends(get_db),
) -> dict:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=422, detail="Invalid coordinates")
    try:
        offer = service.create_aid_offer(
            db, source="web", external_id=client_id, lat=lat, lon=lon,
            category=category, description=description, capacity=capacity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _offer_out(offer)


@router.post("/recovery/missing")
async def submit_missing_person(
    client_id: Annotated[str, Form()],
    report_type: Annotated[str, Form()],
    name: Annotated[str, Form()],
    age: Annotated[int | None, Form()] = None,
    gender: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
    lat: Annotated[float | None, Form()] = None,
    lon: Annotated[float | None, Form()] = None,
    photo: UploadFile | None = None,
    db: Session = Depends(get_db),
) -> dict:
    if (lat is None) != (lon is None):
        raise HTTPException(status_code=422, detail="lat and lon must be given together")
    if lat is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=422, detail="Invalid coordinates")
    media_bytes = await photo.read() if photo is not None else None
    try:
        person = service.create_missing_person(
            db, source="web", external_id=client_id, report_type=report_type, name=name,
            age=age, gender=gender, description=description, lat=lat, lon=lon,
            media_bytes=media_bytes, media_filename=photo.filename if photo else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _missing_out(person)


# --- analyst: damage assessments -----------------------------------------


@router.get("/analyst/recovery/damage")
def list_damage(
    hours: float = 72.0,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    return [_damage_out(a) for a in service.recent_damage_assessments(db, hours=hours)]


@router.post("/analyst/recovery/damage/{assessment_id}/review")
def review_damage(
    assessment_id: uuid.UUID,
    analyst: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    assessment = db.get(DamageAssessment, assessment_id)
    if assessment is None:
        raise HTTPException(status_code=404, detail="Damage assessment not found")
    return _damage_out(service.mark_damage_reviewed(db, assessment, analyst=analyst))


# --- analyst: mutual-aid board --------------------------------------------


@router.get("/analyst/recovery/relief-requests")
def list_relief_requests(
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    return [_relief_out(r) for r in service.open_relief_requests(db)]


@router.post("/analyst/recovery/relief-requests/{request_id}/fulfill")
def fulfill_relief_request(
    request_id: uuid.UUID,
    fulfilled_by: Annotated[str | None, Form()] = None,
    analyst: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    req = db.get(ReliefRequest, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Relief request not found")
    if req.status != "open":
        raise HTTPException(status_code=409, detail="Request is not open")
    return _relief_out(service.fulfill_relief_request(db, req, analyst=analyst, fulfilled_by=fulfilled_by))


@router.get("/analyst/recovery/aid-offers")
def list_aid_offers(
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    return [_offer_out(o) for o in service.open_aid_offers(db)]


@router.post("/analyst/recovery/aid-offers/{offer_id}/close")
def close_aid_offer(
    offer_id: uuid.UUID,
    analyst: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    offer = db.get(AidOffer, offer_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="Aid offer not found")
    if offer.status != "open":
        raise HTTPException(status_code=409, detail="Offer is not open")
    return _offer_out(service.close_aid_offer(db, offer, analyst=analyst))


@router.get("/analyst/recovery/aid-matches")
def aid_matches(
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    return service.suggested_aid_matches(db)


# --- analyst: missing/found-person registry -------------------------------


@router.get("/analyst/recovery/missing")
def list_missing(
    report_type: str | None = None,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    if report_type is not None and report_type not in MISSING_PERSON_TYPES:
        raise HTTPException(status_code=422, detail=f"report_type must be one of {MISSING_PERSON_TYPES}")
    return [_missing_out(p) for p in service.open_missing_persons(db, report_type=report_type)]


@router.get("/analyst/recovery/missing/{person_id}/matches")
def missing_matches(
    person_id: uuid.UUID,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    person = db.get(MissingPerson, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Missing/found entry not found")
    return service.candidate_matches_for(db, person)


@router.post("/analyst/recovery/missing/purge-expired")
def purge_expired_missing(
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    """Manual trigger for the retention job (see core/scheduler.py, which
    also runs this daily) — same "don't wait for the tick" convenience
    /analyst/narratives/detect and the forecast backtest endpoints already
    offer for their own scheduled jobs."""
    return {"purged": service.purge_expired_missing_persons(db)}


@router.post("/analyst/recovery/missing/{person_id}/resolve")
def resolve_missing(
    person_id: uuid.UUID,
    matched_person_id: Annotated[uuid.UUID | None, Form()] = None,
    analyst: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    person = db.get(MissingPerson, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Missing/found entry not found")
    try:
        return _missing_out(
            service.resolve_missing_person(db, person, analyst=analyst, matched_person_id=matched_person_id)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
