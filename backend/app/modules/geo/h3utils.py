import h3

H3_RESOLUTION = 8  # ~0.7 km² hexagons


def cell_for(lat: float, lon: float, resolution: int = H3_RESOLUTION) -> str:
    return h3.latlng_to_cell(lat, lon, resolution)


def cell_centroid(cell: str) -> tuple[float, float]:
    """Return (lat, lon) of the cell center — used to fuzz public locations."""
    return h3.cell_to_latlng(cell)


def cell_polygon(cell: str) -> list[list[float]]:
    """GeoJSON ring ([lon, lat] pairs, closed) for a cell boundary."""
    boundary = h3.cell_to_boundary(cell)  # ((lat, lon), ...)
    ring = [[lon, lat] for lat, lon in boundary]
    ring.append(ring[0])
    return ring


def cell_to_parent(cell: str, resolution: int) -> str:
    """Coarsen a cell to an ancestor resolution — used by the open-data
    aggregation pipeline (phase 4, milestone 3) to group reports into larger
    cells than the internal res-8 grid before any k-anonymity/DP step, since
    a bigger cell means a bigger natural group size."""
    return h3.cell_to_parent(cell, resolution)
