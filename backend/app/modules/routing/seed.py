"""Pilot shelter seed data — a small hand-picked list of illustrative
locations near this deployment's other pilot points (fisherman/pfz.py's
landing sites, ivr/locations.py's coastal locations), standing in for a real
government shelter registry import. Same honest-stub role those play
elsewhere in this project — analyst CRUD (routing/router.py) is the real,
ongoing way shelters get added and kept current.

Unlike sync_stations()/seed_corpus() (which upsert by a known id on every
startup, since those are reference config, not analyst-editable), this only
seeds once: shelters are meant to be edited/closed/reopened by analysts, so
a restart must never silently overwrite that state back to the seed list.
"""
import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Shelter

log = logging.getLogger(__name__)

SEED_PATH = Path(__file__).parent / "shelters_seed.json"


def seed_shelters(db: Session) -> None:
    if db.query(Shelter).count() > 0:
        return
    with open(SEED_PATH, encoding="utf-8") as f:
        entries = json.load(f)
    for e in entries:
        db.add(
            Shelter(
                name=e["name"],
                lat=e["lat"],
                lon=e["lon"],
                geom=f"SRID=4326;POINT({e['lon']} {e['lat']})",
                capacity=e.get("capacity"),
                status=e.get("status", "open"),
                address=e.get("address"),
            )
        )
    db.commit()
    log.info("Seeded %d pilot shelters", len(entries))
