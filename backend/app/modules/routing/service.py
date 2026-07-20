"""Shelter directory + route-to-safety.

Finds the nearest open shelter from a point and asks Valhalla for a path
there, routed around active hazard geometry. That exclusion list is built
only from incidents that already cleared this app's escalation gate
(corroborated/verified — instrument, satellite, or analyst agreement) and
analyst-issued warning alerts — never from raw citizen report volume alone,
the same "no citizen-only escalation" rule the scoring and alerts modules
enforce.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Alert, Incident, Shelter
from app.modules.geo.distance import haversine_km
from app.modules.geo.h3utils import cell_polygon
from app.modules.routing import client
from app.modules.routing.client import RoutingUnavailable
from app.modules.routing.polyline import decode_polyline6

log = logging.getLogger(__name__)

_BLOCKING_INCIDENT_STATUSES = ("corroborated", "verified")


def list_shelters(db: Session, status: str | None = None) -> list[Shelter]:
    q = select(Shelter).order_by(Shelter.name)
    if status:
        q = q.where(Shelter.status == status)
    return list(db.scalars(q).all())


def create_shelter(
    db: Session, *, name: str, lat: float, lon: float,
    capacity: int | None = None, status: str = "open", address: str | None = None,
) -> Shelter:
    shelter = Shelter(
        name=name, lat=lat, lon=lon, geom=f"SRID=4326;POINT({lon} {lat})",
        capacity=capacity, status=status, address=address,
    )
    db.add(shelter)
    db.commit()
    return shelter


def update_shelter(db: Session, shelter: Shelter, **fields) -> Shelter:
    for key, value in fields.items():
        if value is not None:
            setattr(shelter, key, value)
    if "lat" in fields or "lon" in fields:
        shelter.geom = f"SRID=4326;POINT({shelter.lon} {shelter.lat})"
    db.commit()
    return shelter


def delete_shelter(db: Session, shelter: Shelter) -> None:
    db.delete(shelter)
    db.commit()


def nearest_open_shelter(db: Session, lat: float, lon: float) -> Shelter | None:
    open_shelters = list_shelters(db, status="open")
    if not open_shelters:
        return None
    return min(open_shelters, key=lambda s: haversine_km(lat, lon, s.lat, s.lon))


def exclude_polygons(db: Session) -> list[list[list[float]]]:
    """H3 cell rings for every hazard cell currently worth routing around."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=settings.routing_active_incident_hours)
    cells: set[str] = set()

    incidents = db.scalars(
        select(Incident)
        .where(Incident.status.in_(_BLOCKING_INCIDENT_STATUSES))
        .where(Incident.last_seen >= cutoff)
    ).all()
    for inc in incidents:
        cells.update(inc.h3_cells or [])

    warnings = db.scalars(
        select(Alert).where(Alert.tier == "warning").where(Alert.status == "active")
    ).all()
    for alert in warnings:
        cells.update(alert.h3_cells or [])

    return [cell_polygon(cell) for cell in cells]


def route_to_safety(db: Session, lat: float, lon: float, costing: str | None = None) -> dict:
    """Nearest open shelter + a Valhalla path there, excluding active hazard
    cells. Returns `{"shelter": None, "route": None, ...}` if there's no open
    shelter to route to at all — that's a real state (no shelters seeded/
    configured yet, or every known one is full/closed), not an error."""
    settings = get_settings()
    shelter = nearest_open_shelter(db, lat, lon)
    if shelter is None:
        return {"shelter": None, "route": None, "excluded_cells": 0, "avoided_hazards": False}

    polygons = exclude_polygons(db)
    locations = [{"lat": lat, "lon": lon}, {"lat": shelter.lat, "lon": shelter.lon}]
    costing = costing or settings.routing_default_costing
    avoided_hazards = bool(polygons)
    try:
        result = client.route(locations, costing=costing, exclude_polygons=polygons or None)
    except RoutingUnavailable:
        if not polygons:
            raise
        # A hard exclusion can trap a route entirely when the traveler's own
        # starting point sits inside the excluded hazard geometry (Valhalla
        # then has no reachable edge outside it to even leave from) — a route
        # that passes near the hazard is still far more useful to someone
        # evacuating than no route at all, so retry without the exclusion.
        log.warning("Route excluding hazard cells failed; retrying without exclusion")
        result = client.route(locations, costing=costing, exclude_polygons=None)
        avoided_hazards = False

    leg = result["trip"]["legs"][0]
    summary = result["trip"]["summary"]
    coordinates = decode_polyline6(leg["shape"])

    return {
        "shelter": {
            "id": str(shelter.id),
            "name": shelter.name,
            "lat": shelter.lat,
            "lon": shelter.lon,
            "capacity": shelter.capacity,
            "status": shelter.status,
            "address": shelter.address,
        },
        "route": {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coordinates},
            "properties": {
                "distance_km": round(summary["length"], 2),
                "duration_min": round(summary["time"] / 60, 1),
            },
        },
        "excluded_cells": len(polygons),
        "avoided_hazards": avoided_hazards,
    }
