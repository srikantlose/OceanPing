"""Decodes Valhalla's route shape — Google's polyline algorithm at 1e6
precision instead of the usual 1e5. No dependency needed; this is ~15 lines
of well-known bit-twiddling."""


def decode_polyline6(encoded: str) -> list[list[float]]:
    """Returns [lon, lat] pairs (GeoJSON coordinate order)."""
    coords = []
    index = lat = lon = 0
    length = len(encoded)
    while index < length:
        for is_lat in (True, False):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if is_lat:
                lat += delta
            else:
                lon += delta
        coords.append([lon / 1e6, lat / 1e6])
    return coords
