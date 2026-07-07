"""Hotspot detection: HDBSCAN over the recent report window.

Coordinates are projected to approximate kilometres (equirectangular) so the
clusterer works in metric space; hulls are returned as GeoJSON features.
"""
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np
from shapely.geometry import MultiPoint, mapping
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.redisclient import get_redis
from app.models import Report

CACHE_KEY = "hotspots:geojson"
CACHE_TTL_SECONDS = 60
HULL_BUFFER_DEG = 0.005  # ~500 m visual buffer around hull


def _project_km(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    lat0 = float(np.mean(lats))
    x = lons * 111.320 * math.cos(math.radians(lat0))
    y = lats * 110.574
    return np.column_stack([x, y])


def compute_hotspots(db: Session) -> dict:
    settings = get_settings()
    since = datetime.now(timezone.utc) - timedelta(hours=settings.hotspot_window_hours)
    rows = db.execute(
        select(Report.lat, Report.lon, Report.confidence, Report.hazard_type)
        .where(Report.created_at >= since)
        .where(Report.status != "rejected")
    ).all()

    features = []
    if len(rows) >= settings.hotspot_min_cluster_size:
        lats = np.array([r.lat for r in rows])
        lons = np.array([r.lon for r in rows])
        coords_km = _project_km(lats, lons)

        from sklearn.cluster import HDBSCAN

        labels = HDBSCAN(min_cluster_size=settings.hotspot_min_cluster_size).fit_predict(coords_km)
        for label in sorted(set(labels)):
            if label == -1:
                continue
            idx = np.where(labels == label)[0]
            points = MultiPoint([(float(lons[i]), float(lats[i])) for i in idx])
            hull = points.convex_hull.buffer(HULL_BUFFER_DEG)
            hazards = Counter(rows[i].hazard_type for i in idx)
            features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(hull),
                    "properties": {
                        "report_count": int(len(idx)),
                        "intensity": round(float(sum(rows[i].confidence for i in idx)), 3),
                        "dominant_hazard": hazards.most_common(1)[0][0],
                        "hazards": dict(hazards),
                    },
                }
            )

    return {"type": "FeatureCollection", "features": features}


def hotspots_geojson(db: Session) -> dict:
    r = get_redis()
    try:
        cached = r.get(CACHE_KEY)
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    result = compute_hotspots(db)
    try:
        r.set(CACHE_KEY, json.dumps(result), ex=CACHE_TTL_SECONDS)
    except Exception:
        pass
    return result
