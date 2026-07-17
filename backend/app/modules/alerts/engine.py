"""Alert tier model — pure functions, no I/O.

Tiers: advisory < watch < warning. `eligible_tier()` is the automatic-escalation
path and is structurally incapable of returning "warning" — that tier only
exists via `alerts/service.py::issue_warning()`, which requires an analyst
identity. This mirrors the no-citizen-only-escalation rule already enforced
for report status in `scoring/service.py`.
"""

TIER_RANK = {"advisory": 0, "watch": 1, "warning": 2}

HAZARD_LABELS = {
    "coastal_flooding": "Coastal flooding",
    "storm_surge": "Storm surge",
    "high_waves": "High waves",
    "tsunami": "Tsunami signs",
    "rip_current": "Rip current",
    "oil_spill": "Oil spill",
    "algal_bloom": "Algal bloom",
    "erosion": "Coastal erosion",
    "other": "Coastal hazard",
}

TIER_EMOJI = {"advisory": "\U0001F535", "watch": "\U0001F7E1", "warning": "\U0001F534"}


def eligible_tier(
    incident_status: str,
    n_independent_reporters: int,
    max_instrument_component: float,
    min_watch_reporters: int,
) -> str | None:
    """Automatic tier this incident currently qualifies for, or None.

    Never returns "warning" — that tier is analyst-only by construction.
    """
    if incident_status not in ("corroborated", "verified"):
        return None
    if max_instrument_component > 0 and n_independent_reporters >= min_watch_reporters:
        return "watch"
    return "advisory"


def draft_message(hazard_type: str, tier: str, report_count: int, note: str | None = None) -> dict:
    label = HAZARD_LABELS.get(hazard_type, hazard_type.replace("_", " ").title())
    text = (
        f"{TIER_EMOJI[tier]} {tier.upper()}: {label} reported near your area "
        f"({report_count} report{'s' if report_count != 1 else ''})."
    )
    if note:
        text += f" {note}"
    return {"en": text}
