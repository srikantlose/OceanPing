"""Potential Fishing Zone (PFZ) advisory sourcing.

INCOIS does publish real PFZ advisories — https://incois.gov.in/MarineFisheries/PfzAdvisory,
covering 14 coastal sectors with the exact shape this app would want (lat/lon, depth,
and distance/bearing from named landing sites). Investigated live before writing any
code here, same as milestone 1's ERDDAP check: the page is a session-driven JS form
(sector + language dropdowns) with no discoverable JSON/API endpoint, and the advisory
content it renders is a static image (`MFS_English.jpg`), not structured text. INCOIS's
own documentation confirms the real dissemination channels for this product are
telephone/fax/e-mail/radio/doordarshan — not a machine-readable feed. Same conclusion
milestone 1 reached about tide-gauge data: nothing here to scrape reliably.

StubPfzProvider is a deterministic local stand-in, not a "real provider with a
credential gate" like satellite/providers.py's SentinelHubProvider/EarthEngineProvider
shells — there's no known real integration point to gate a shell against, so building
one would just be an empty class with nothing to verify. Ships as the only provider,
documented as such, same honesty call as everywhere else in this project.
"""
import hashlib
import random
from datetime import datetime, timezone

PILOT_SECTOR = "North Tamil Nadu"  # matches this deployment's pilot area (Chennai coast)

# Named landing sites PFZ distances/bearings are reported relative to — same
# honest role as ivr/locations.py's pilot location list.
LANDING_SITES = [
    {"name": "Kasimedu", "lat": 13.1167, "lon": 80.2967},
    {"name": "Ennore", "lat": 13.2146, "lon": 80.3223},
    {"name": "Injambakkam", "lat": 12.9243, "lon": 80.2472},
]

_BEARINGS = ["N", "NE", "E", "SE", "S"]


class StubPfzProvider:
    """Deterministic pseudo-zones, seeded by sector + ISO week so results are
    stable within a forecast-validity window then rotate — same role
    satellite/providers.py::StubProvider plays for scene scoring."""

    name = "stub"

    def fetch(self, sector: str) -> list[dict]:
        now = datetime.now(timezone.utc)
        seed = f"{sector}:{now.isocalendar()[1]}"
        rng = random.Random(hashlib.sha256(seed.encode()).hexdigest())
        zones = []
        for site in LANDING_SITES:
            distance_km = round(rng.uniform(15, 55), 1)
            bearing = rng.choice(_BEARINGS)
            zones.append(
                {
                    "lat": round(site["lat"] + rng.uniform(-0.3, 0.3), 4),
                    "lon": round(site["lon"] + rng.uniform(0.2, 0.6), 4),
                    "depth_m": round(rng.uniform(30, 90), 1),
                    "distance_km": distance_km,
                    "bearing": f"{distance_km} km {bearing} of {site['name']}",
                }
            )
        return zones


def get_provider() -> StubPfzProvider:
    return StubPfzProvider()  # only provider implemented — see module docstring
