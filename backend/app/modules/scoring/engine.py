"""Confidence engine v1 — pure functions, no I/O.

confidence = 0.20*trust + 0.25*coherence + 0.25*instrument + 0.15*media
             + 0.10*satellite + 0.05*account_device

Six-signal rebalance (phase 2, milestone 2): satellite and account/device
join as slow, non-citizen-controlled corroboration signals. Satellite
latency is hours, so it corroborates slow hazards, never gates fast ones —
hazards with no satellite recipe (see satellite/providers.py::HAZARD_RECIPES)
just carry a 0 there, same as instrument does today for oil_spill.

Hard rule enforced by the service layer: report volume alone can never
escalate status — "corroborated" requires instrument agreement, "verified"
requires an analyst.
"""

WEIGHTS = {
    "trust": 0.20,
    "coherence": 0.25,
    "instrument": 0.25,
    "media": 0.15,
    "satellite": 0.10,
    "account_device": 0.05,
}

# Which instrument variables can corroborate which hazard claims.
HAZARD_VARIABLES: dict[str, set[str]] = {
    "coastal_flooding": {"water_level", "wave_height", "air_pressure"},
    "storm_surge": {"water_level", "wave_height", "air_pressure"},
    "high_waves": {"wave_height", "water_level"},
    "tsunami": {"water_level", "wave_height"},
    "rip_current": {"wave_height"},
    "oil_spill": set(),      # no instrument signal — satellite (HAZARD_RECIPES) corroborates instead
    "algal_bloom": set(),    # same — chlorophyll/NDCI anomaly is the corroboration path
    "erosion": {"wave_height", "water_level"},
    "other": {"water_level", "wave_height", "air_pressure"},
}

MEDIA_NEUTRAL = 0.5  # score for reports without any media attached
HEARSAY_DISCOUNT = 0.5  # a secondhand account counts for half its coherence contribution


def coherence_score(n_independent: int, hearsay: bool = False) -> float:
    """0 with no independent corroboration; saturates at 4+ nearby reporters.
    A hearsay report (secondhand account, not a firsthand observation) has this
    halved — it isn't independent corroboration in the same sense."""
    if n_independent <= 0:
        return 0.0
    score = min(1.0, 0.4 + 0.2 * (n_independent - 1))
    return score * HEARSAY_DISCOUNT if hearsay else score


def instrument_score(zscores: list[float]) -> float:
    """Map the strongest hazard-consistent anomaly to [0,1]; |z|=2.5 → 0, |z|>=5 → 1."""
    if not zscores:
        return 0.0
    strongest = max(abs(z) for z in zscores)
    return max(0.0, min(1.0, (strongest - 2.5) / 2.5))


def media_score(
    has_media: bool,
    phash_reused: bool = False,
    exif_gps_km: float | None = None,
    exif_time_offset_hours: float | None = None,
) -> float:
    """Forensics: recycled media is disqualifying; EXIF inconsistencies penalize;
    missing EXIF is mildly suspicious; no media is neutral."""
    if not has_media:
        return MEDIA_NEUTRAL
    if phash_reused:
        return 0.0
    if exif_gps_km is not None and exif_gps_km > 5.0:
        return 0.2
    if exif_time_offset_hours is not None and exif_time_offset_hours > 6.0:
        return 0.4
    if exif_gps_km is None and exif_time_offset_hours is None:
        return 0.6
    return 1.0


def satellite_score(scores: list[float]) -> float:
    """Strongest corroborating scene score in [0,1]; 0 with no observations
    yet. Satellite passes are hours apart, so most reports won't have one at
    their first rescore — that's an absence of evidence, not evidence
    against, so it stays 0 rather than neutral (same call as instrument_score
    makes for hazards it can't corroborate)."""
    return max(scores) if scores else 0.0


ACCOUNT_AGE_SATURATION_HOURS = 24 * 7  # a week-old account scores like any established one
ACCOUNT_BURST_PENALTY_PER_REPORT = 0.15
ACCOUNT_BURST_PENALTY_MAX = 0.5


def account_device_score(account_age_hours: float, recent_report_count: int) -> float:
    """Older accounts score higher (saturates at a week old); a burst of
    reports from the same account inside the rate-limit window (see
    ingest/service.py's rl:rep: counter) looks automated or coordinated and
    pulls the score down. recent_report_count is that counter's value, so 1
    (just this report) carries no penalty."""
    age = max(0.0, min(1.0, account_age_hours / ACCOUNT_AGE_SATURATION_HOURS))
    burst_penalty = min(
        ACCOUNT_BURST_PENALTY_MAX, max(0, recent_report_count - 1) * ACCOUNT_BURST_PENALTY_PER_REPORT
    )
    return round(max(0.0, age - burst_penalty), 4)


def combine(components: dict[str, float]) -> float:
    total = sum(WEIGHTS[k] * components.get(k, 0.0) for k in WEIGHTS)
    return round(max(0.0, min(1.0, total)), 4)
