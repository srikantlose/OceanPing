"""Valhalla routing engine HTTP client — plain httpx, same style as every
other adapter in this app. Unlike those, there's no credential gate here:
Valhalla is a real, self-hosted routing engine (see docker/valhalla/ and
scripts/routing/fetch_osm_extract.sh), not a stub. The gate is reachability
instead — its tiles need a one-time build after the OSM extract script runs,
so a connection failure is a real, expected degrade mode until that
finishes, not a bug to mask."""
import logging

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)


class RoutingUnavailable(Exception):
    pass


def route(
    locations: list[dict],
    costing: str = "pedestrian",
    exclude_polygons: list | None = None,
) -> dict:
    settings = get_settings()
    body: dict = {"locations": locations, "costing": costing, "id": "oceanping"}
    if exclude_polygons:
        body["exclude_polygons"] = exclude_polygons
    try:
        resp = httpx.post(f"{settings.valhalla_url}/route", json=body, timeout=10.0)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300]
        raise RoutingUnavailable(f"Valhalla returned {exc.response.status_code}: {detail}") from exc
    except httpx.HTTPError as exc:
        raise RoutingUnavailable(f"Valhalla unreachable: {exc}") from exc
    data = resp.json()
    if "trip" not in data:
        raise RoutingUnavailable(f"Valhalla returned no trip: {data.get('error', data)}")
    return data
