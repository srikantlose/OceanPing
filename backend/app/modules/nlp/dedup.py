"""Semantic + spatiotemporal dedup: merge duplicate reports into incidents.

A report joins an existing incident when all hold:
  - same hazard type
  - incident seen within the last `incident_window_hours`
  - spatial: report cell within 1 H3 ring of any incident cell
  - semantic: if both sides have embeddings, cosine similarity >= SIM_THRESHOLD
The report count on an incident then acts as a corroboration signal, not noise.
"""
from datetime import timedelta

import h3
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Incident, Report

SIM_THRESHOLD = 0.5


def cosine(a, b) -> float:
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0:
        return 0.0
    return float(va @ vb / denom)


def spatially_adjacent(report_cell: str, incident_cells: list[str]) -> bool:
    neighborhood = set(h3.grid_disk(report_cell, 1))
    return any(c in neighborhood for c in incident_cells)


def should_merge(
    report_cell: str,
    incident_cells: list[str],
    report_embedding=None,
    incident_embedding=None,
) -> bool:
    if not spatially_adjacent(report_cell, incident_cells):
        return False
    if report_embedding is not None and incident_embedding is not None:
        return cosine(report_embedding, incident_embedding) >= SIM_THRESHOLD
    return True


def assign_incident(db: Session, report: Report) -> Incident:
    """Attach report to a matching incident or create a new one. Flushes, no commit."""
    settings = get_settings()
    window_start = report.created_at - timedelta(hours=settings.incident_window_hours)
    candidates = db.scalars(
        select(Incident)
        .where(Incident.hazard_type == report.hazard_type)
        .where(Incident.last_seen >= window_start)
        .order_by(Incident.last_seen.desc())
        .limit(50)
    ).all()

    rep_emb = None if report.embedding is None else np.asarray(report.embedding, dtype=float)

    for inc in candidates:
        inc_emb = None if inc.centroid_embedding is None else np.asarray(inc.centroid_embedding, dtype=float)
        if should_merge(report.h3_cell, inc.h3_cells or [], rep_emb, inc_emb):
            n = inc.report_count
            inc.centroid_lat = (inc.centroid_lat * n + report.lat) / (n + 1)
            inc.centroid_lon = (inc.centroid_lon * n + report.lon) / (n + 1)
            if rep_emb is not None:
                if inc_emb is None:
                    new_emb = rep_emb
                else:
                    new_emb = (inc_emb * n + rep_emb) / (n + 1)
                    norm = np.linalg.norm(new_emb)
                    if norm > 0:
                        new_emb = new_emb / norm
                inc.centroid_embedding = new_emb.tolist()
            inc.report_count = n + 1
            if report.h3_cell not in (inc.h3_cells or []):
                inc.h3_cells = list(inc.h3_cells or []) + [report.h3_cell]
            inc.last_seen = max(inc.last_seen, report.created_at)
            report.incident = inc
            db.flush()
            return inc

    inc = Incident(
        hazard_type=report.hazard_type,
        h3_cells=[report.h3_cell],
        centroid_lat=report.lat,
        centroid_lon=report.lon,
        report_count=1,
        centroid_embedding=report.embedding,
        first_seen=report.created_at,
        last_seen=report.created_at,
    )
    db.add(inc)
    report.incident = inc
    db.flush()
    return inc
