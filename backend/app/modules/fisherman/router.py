"""Public "give before ask" surfaces for fisherman mode: PFZ zones and
nearby sea-state, reachable without a report ever being filed. Same public
trust boundary as /map/* — no analyst auth."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.fisherman import pfz as pfz_mod
from app.modules.fisherman import service

router = APIRouter(prefix="/sea", tags=["fisherman"])


@router.get("/pfz")
def pfz_advisories(sector: str = pfz_mod.PILOT_SECTOR, db: Session = Depends(get_db)) -> dict:
    return {"sector": sector, "zones": service.active_pfz_advisories(db, sector)}


@router.get("/state")
def sea_state(lat: float | None = None, lon: float | None = None, db: Session = Depends(get_db)) -> dict:
    reading = service.nearest_station_reading(db, lat, lon)
    return {"station": reading}
