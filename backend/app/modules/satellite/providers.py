"""Satellite scene providers behind one interface, mirroring how
delivery/adapters.py lets the SMS provider swap between a local console stub
and a real Twilio/Exotel account without touching the worker. Only
StubProvider actually runs in this environment: it's deterministic and needs
no credentials, so drills and tests can exercise the full satellite ->
scoring path. SentinelHubProvider / EarthEngineProvider have the real
interface shape and a real credential gate (same pattern as
delivery/adapters.py's TwilioAdapter/ExotelAdapter), but deriving a defensible
dark-slick / NDCI / water-extent score from raw Sentinel imagery is a raster-
processing task this environment has no account or way to verify — so that
part is deferred rather than shipped unverified (see the phase-2 plan).
"""
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from app.core.config import get_settings
from app.modules.hazards.registry import satellite_recipes_table

log = logging.getLogger(__name__)

# Which hazard types have a satellite recipe at all, mirroring
# scoring/engine.py::HAZARD_VARIABLES — hazards with no recipe here never
# get a satellite component above 0, and the poll job skips them outright.
# Sourced from the hazard registry (phase 4, milestone 2) — a new hazard's
# recipe (or lack of one) is a config file, not a code edit here.
HAZARD_RECIPES: dict[str, str] = satellite_recipes_table()


@dataclass
class ScanResult:
    provider: str
    recipe: str
    score: float  # 0..1, corroboration strength
    scene_time: datetime
    scene_url: str | None = None


class SatelliteProvider(Protocol):
    def observe(self, incident, recipe: str) -> ScanResult | None: ...


class StubProvider:
    """Deterministic local/dev stand-in: derives a reproducible score from the
    incident id + recipe (sha256 hash -> [0,1]) so drills and tests get a
    stable, fake-but-plausible satellite observation without any real
    Earth Observation account."""

    name = "stub"

    def observe(self, incident, recipe: str) -> ScanResult | None:
        digest = hashlib.sha256(f"{incident.id}:{recipe}".encode()).hexdigest()
        score = (int(digest[:8], 16) % 1000) / 1000.0
        return ScanResult(
            provider=self.name,
            recipe=recipe,
            score=score,
            scene_time=datetime.now(timezone.utc),
            scene_url=f"stub://{recipe}/{incident.id}",
        )


class SentinelHubProvider:
    """Real shape (client-credentials check), scene scoring deferred — see
    module docstring. Never raises into the polling job: an unconfigured or
    not-yet-implemented provider just means no observation this cycle."""

    name = "sentinel_hub"

    def observe(self, incident, recipe: str) -> ScanResult | None:
        settings = get_settings()
        if not (settings.sentinel_hub_client_id and settings.sentinel_hub_client_secret):
            log.info("SentinelHubProvider skipped: credentials not configured")
            return None
        log.warning("SentinelHubProvider has no scene-scoring recipe implemented yet")
        return None


class EarthEngineProvider:
    """Same shape and same deferral as SentinelHubProvider, gated on a service
    account credentials file instead of a client id/secret pair."""

    name = "earth_engine"

    def observe(self, incident, recipe: str) -> ScanResult | None:
        settings = get_settings()
        if not settings.earth_engine_service_account_json:
            log.info("EarthEngineProvider skipped: credentials not configured")
            return None
        log.warning("EarthEngineProvider has no scene-scoring recipe implemented yet")
        return None


_PROVIDERS: dict[str, type] = {
    "stub": StubProvider,
    "sentinel_hub": SentinelHubProvider,
    "earth_engine": EarthEngineProvider,
}


def get_provider() -> SatelliteProvider:
    cls = _PROVIDERS.get(get_settings().satellite_provider, StubProvider)
    return cls()
