"""Analyst-only SITREP endpoints. Generation is normally automatic (hourly,
see core/scheduler.py) — this exposes the same generate_sitrep() for an
analyst who doesn't want to wait for the next tick (and for the drill), plus
the one-click file action the plan calls for.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import require_analyst
from app.models import Sitrep
from app.modules.sitrep.service import file_sitrep, generate_sitrep

router = APIRouter(tags=["sitrep"])


def _sitrep_out(s: Sitrep) -> dict:
    return {
        "id": str(s.id),
        "period_start": s.period_start.isoformat(),
        "period_end": s.period_end.isoformat(),
        "status": s.status,
        "content": s.content,
        "data_snapshot_hash": s.data_snapshot_hash,
        "generated_at": s.generated_at.isoformat(),
        "filed_by": s.filed_by,
        "filed_at": s.filed_at.isoformat() if s.filed_at else None,
    }


@router.get("/analyst/sitreps")
def list_sitreps(
    limit: int = 20,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.scalars(select(Sitrep).order_by(Sitrep.generated_at.desc()).limit(min(limit, 200))).all()
    return [_sitrep_out(s) for s in rows]


@router.post("/analyst/sitreps/generate")
def generate_sitrep_endpoint(
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    return _sitrep_out(generate_sitrep(db))


@router.post("/analyst/sitreps/{sitrep_id}/file")
def file_sitrep_endpoint(
    sitrep_id: uuid.UUID,
    analyst: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> dict:
    sitrep = db.get(Sitrep, sitrep_id)
    if sitrep is None:
        raise HTTPException(status_code=404, detail="SITREP not found")
    try:
        return _sitrep_out(file_sitrep(db, sitrep, analyst=analyst))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
