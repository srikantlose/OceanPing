import h3

from app.modules.geo.h3utils import cell_for


def cells_around(lat: float, lon: float, rings: int) -> list[str]:
    """H3 cells within `rings` hops of the cell containing (lat, lon) —
    used to turn a subscriber's shared location into a geofence."""
    center = cell_for(lat, lon)
    return list(h3.grid_disk(center, rings))
