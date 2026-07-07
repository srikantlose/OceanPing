from app.modules.geo.h3utils import cell_centroid, cell_for, cell_polygon
from app.modules.ingest.media import haversine_km

CHENNAI = (13.0827, 80.2707)


def test_cell_roundtrip_close_to_origin():
    cell = cell_for(*CHENNAI)
    lat, lon = cell_centroid(cell)
    # res-8 hexes are ~460 m edge; centroid must be within one cell of the point
    assert haversine_km(CHENNAI[0], CHENNAI[1], lat, lon) < 1.0


def test_nearby_points_share_or_neighbor_cells():
    a = cell_for(13.0827, 80.2707)
    b = cell_for(13.0830, 80.2710)  # ~40 m away
    assert a == b


def test_cell_polygon_is_closed_ring():
    ring = cell_polygon(cell_for(*CHENNAI))
    assert len(ring) == 7  # hexagon + closing point
    assert ring[0] == ring[-1]
    for lon, lat in ring:
        assert -180 <= lon <= 180 and -90 <= lat <= 90
