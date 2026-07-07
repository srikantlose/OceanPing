"""Confidence engine v1 — pure functions, no I/O.

confidence = 0.25*trust + 0.30*coherence + 0.30*instrument + 0.15*media

Hard rule enforced by the service layer: report volume alone can never
escalate status — "corroborated" requires instrument agreement, "verified"
requires an analyst.
"""

WEIGHTS = {
    "trust": 0.25,
    "coherence": 0.30,
    "instrument": 0.30,
    "media": 0.15,
}

# Which instrument variables can corroborate which hazard claims.
HAZARD_VARIABLES: dict[str, set[str]] = {
    "coastal_flooding": {"water_level", "wave_height", "air_pressure"},
    "storm_surge": {"water_level", "wave_height", "air_pressure"},
    "high_waves": {"wave_height", "water_level"},
    "tsunami": {"water_level", "wave_height"},
    "rip_current": {"wave_height"},
    "oil_spill": set(),      # needs satellite/CV, out of MVP scope
    "algal_bloom": set(),
    "erosion": {"wave_height", "water_level"},
    "other": {"water_level", "wave_height", "air_pressure"},
}

MEDIA_NEUTRAL = 0.5  # score for reports without any media attached


def coherence_score(n_independent: int) -> float:
    """0 with no independent corroboration; saturates at 4+ nearby reporters."""
    if n_independent <= 0:
        return 0.0
    return min(1.0, 0.4 + 0.2 * (n_independent - 1))


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


def combine(components: dict[str, float]) -> float:
    total = sum(WEIGHTS[k] * components.get(k, 0.0) for k in WEIGHTS)
    return round(max(0.0, min(1.0, total)), 4)
