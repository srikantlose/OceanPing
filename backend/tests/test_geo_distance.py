from app.modules.geo.distance import haversine_km


def test_haversine_km_zero_for_same_point():
    assert haversine_km(13.05, 80.28, 13.05, 80.28) == 0.0


def test_haversine_km_known_distance_chennai_to_bangalore():
    # Chennai to Bangalore is ~290 km as the crow flies.
    km = haversine_km(13.0827, 80.2707, 12.9716, 77.5946)
    assert 280 < km < 300
