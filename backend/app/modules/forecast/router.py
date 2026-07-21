from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import require_analyst
from app.models import Forecast
from app.modules.forecast.service import (
    accuracy_summary,
    generate_propagation_forecasts,
    generate_sensor_forecasts,
)
from app.modules.geo.h3utils import cell_polygon

router = APIRouter(tags=["forecast"])

# The dashed propagation layer only shows forecasts still within their
# furthest horizon — a 6h-old forecast about where a front would be 1-3h
# later is stale, not a live prediction.
PROPAGATION_MAP_MAX_AGE_HOURS = 3.0


def _forecast_out(f: Forecast) -> dict:
    return {
        "id": str(f.id),
        "kind": f.kind,
        "subject_type": f.subject_type,
        "subject_id": f.subject_id,
        "hazard_type": f.hazard_type,
        "generated_at": f.generated_at.isoformat(),
        "horizon_hours": f.horizon_hours,
        "content": f.content,
        "validation": f.validation,
        "validated_at": f.validated_at.isoformat() if f.validated_at else None,
    }


@router.get("/analyst/forecasts")
def list_forecasts(
    kind: str | None = None,
    limit: int = 50,
    _: str = Depends(require_analyst),
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(Forecast).order_by(Forecast.generated_at.desc()).limit(min(limit, 200))
    if kind:
        stmt = stmt.where(Forecast.kind == kind)
    return [_forecast_out(f) for f in db.scalars(stmt).all()]


@router.post("/analyst/forecasts/generate")
def generate_forecasts_endpoint(_: str = Depends(require_analyst), db: Session = Depends(get_db)) -> dict:
    return {
        "sensor_forecasts": generate_sensor_forecasts(db),
        "propagation_forecasts": generate_propagation_forecasts(db),
    }


@router.get("/map/propagation")
def public_propagation(db: Session = Depends(get_db)) -> dict:
    """Latest still-current propagation forecast per incident, as a dashed
    "projected" layer — cells the hazard front is headed toward, not yet
    backed by any report."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=PROPAGATION_MAP_MAX_AGE_HOURS)
    forecasts = db.scalars(
        select(Forecast)
        .where(Forecast.kind == "propagation")
        .where(Forecast.generated_at >= cutoff)
        .order_by(Forecast.generated_at.desc())
    ).all()
    latest_by_incident: dict[str, Forecast] = {}
    for f in forecasts:
        latest_by_incident.setdefault(f.subject_id, f)  # ordered desc, so first hit is newest

    features = []
    for f in latest_by_incident.values():
        front = f.content.get("front", {})
        for horizon, cells in (f.content.get("projected") or {}).items():
            for cell in cells:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "Polygon", "coordinates": [cell_polygon(cell)]},
                        "properties": {
                            "incident_id": f.subject_id,
                            "hazard_type": f.hazard_type,
                            "horizon_hours": float(horizon),
                            "speed_kmh": front.get("speed_kmh"),
                        },
                    }
                )
    return {"type": "FeatureCollection", "features": features}


@router.get("/forecasts/accuracy")
def forecast_accuracy(db: Session = Depends(get_db)) -> dict:
    """Public 'how right were we' rollup per named pilot location — see
    modules/forecast/service.py::accuracy_summary."""
    return accuracy_summary(db)
