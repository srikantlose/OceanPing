"""Shelter CRUD (analyst-only, same trust boundary as /analyst/*) and the
public route-to-safety endpoint (no analyst auth, same boundary as /chat and
/sea/* — a citizen asking "how do I get to safety" needs no login)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import require_analyst
from app.models import Shelter
from app.modules.routing import service
from app.modules.routing.client import RoutingUnavailable

router = APIRouter(tags=["routing"])


def _shelter_detail(shelter: Shelter) -> dict:
    return {
        "id": str(shelter.id),
        "name": shelter.name,
        "lat": shelter.lat,
        "lon": shelter.lon,
        "capacity": shelter.capacity,
        "status": shelter.status,
        "address": shelter.address,
    }


class ShelterIn(BaseModel):
    name: str
    lat: float
    lon: float
    capacity: int | None = None
    status: str = "open"
    address: str | None = None


class ShelterUpdateIn(BaseModel):
    name: str | None = None
    lat: float | None = None
    lon: float | None = None
    capacity: int | None = None
    status: str | None = None
    address: str | None = None


@router.get("/analyst/shelters")
def list_shelters(_: str = Depends(require_analyst), db: Session = Depends(get_db)) -> list[dict]:
    return [_shelter_detail(s) for s in service.list_shelters(db)]


@router.post("/analyst/shelters")
def create_shelter(
    body: ShelterIn, _: str = Depends(require_analyst), db: Session = Depends(get_db)
) -> dict:
    shelter = service.create_shelter(db, **body.model_dump())
    return _shelter_detail(shelter)


@router.patch("/analyst/shelters/{shelter_id}")
def update_shelter(
    shelter_id: uuid.UUID, body: ShelterUpdateIn,
    _: str = Depends(require_analyst), db: Session = Depends(get_db),
) -> dict:
    shelter = db.get(Shelter, shelter_id)
    if shelter is None:
        raise HTTPException(status_code=404, detail="Shelter not found")
    shelter = service.update_shelter(db, shelter, **body.model_dump(exclude_unset=True))
    return _shelter_detail(shelter)


@router.delete("/analyst/shelters/{shelter_id}")
def delete_shelter(
    shelter_id: uuid.UUID, _: str = Depends(require_analyst), db: Session = Depends(get_db)
) -> dict:
    shelter = db.get(Shelter, shelter_id)
    if shelter is None:
        raise HTTPException(status_code=404, detail="Shelter not found")
    service.delete_shelter(db, shelter)
    return {"deleted": str(shelter_id)}


@router.get("/route")
def route_to_safety(
    lat: float, lon: float, costing: str | None = None, db: Session = Depends(get_db)
) -> dict:
    try:
        return service.route_to_safety(db, lat, lon, costing=costing)
    except RoutingUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
