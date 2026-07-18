"""Named pilot coastal locations, selectable by a single DTMF digit.

A real IVR deployment would resolve the caller's location from a registered
village (their SIM registration address on file with the cooperative/telco)
or a cell-tower-area lookup — neither is available here (no telco
integration this environment can reach or verify), so callers instead pick
from a short list of real, named coastal landmarks near the pilot district
(Chennai), the same honest stand-in role StubProvider plays for satellite
imagery. Coordinates are neighbourhood-level, not survey-grade.
"""

PILOT_LOCATIONS = [
    {"digit": "1", "name": "Marina Beach", "lat": 13.0500, "lon": 80.2824},
    {"digit": "2", "name": "Besant Nagar / Elliot's Beach", "lat": 13.0002, "lon": 80.2669},
    {"digit": "3", "name": "Kasimedu fishing harbour", "lat": 13.1167, "lon": 80.2967},
    {"digit": "4", "name": "Injambakkam", "lat": 12.9243, "lon": 80.2472},
    {"digit": "5", "name": "Ennore", "lat": 13.2146, "lon": 80.3223},
]

_BY_DIGIT = {loc["digit"]: loc for loc in PILOT_LOCATIONS}


def location_for_digit(digit: str) -> dict | None:
    return _BY_DIGIT.get(digit)
