"""Loads the real, precomputed per-H3-cell elevation table (Copernicus DEM
GLO-30, see scripts/inundation/) into the DB once at startup.

Unlike shelters, this data isn't analyst-editable, but "once, if empty" is
still the right call: there's nothing to reconcile against a live edit, and a
restart shouldn't re-run a load against an already-populated table.
"""
import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import ElevationCell

log = logging.getLogger(__name__)

DATA_PATH = Path(__file__).parent / "elevation_cells_chennai.json"


def seed_elevation_cells(db: Session) -> None:
    if db.query(ElevationCell).count() > 0:
        return
    if not DATA_PATH.exists():
        log.warning(
            "Inundation: no elevation_cells_chennai.json artifact found — skipping seed. "
            "Run scripts/inundation/fetch_dem_extract.sh then build_elevation_cells.sh to generate it."
        )
        return
    with open(DATA_PATH, encoding="utf-8") as f:
        rows = json.load(f)
    for row in rows:
        db.add(ElevationCell(h3_cell=row["h3_cell"], elevation_m=row["elevation_m"]))
    db.commit()
    log.info("Seeded %d elevation cells from Copernicus DEM GLO-30 extract", len(rows))
