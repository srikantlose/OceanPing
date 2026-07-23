"""Alert tier model — pure functions, no I/O.

Tiers: advisory < watch < warning. `eligible_tier()` is the automatic-escalation
path and is structurally incapable of returning "warning" — that tier only
exists via `alerts/service.py::issue_warning()`, which requires an analyst
identity. This mirrors the no-citizen-only-escalation rule already enforced
for report status in `scoring/service.py`.
"""
from app.modules.hazards.registry import SUPPORTED_LANGS, alert_labels_by_lang

TIER_RANK = {"advisory": 0, "watch": 1, "warning": 2}

# Alert body text uses each hazard's curated alert-label copy (see
# modules/hazards/) — English gets its own Title Case phrasing distinct from
# the menu/speech copy; Tamil/Telugu reuse the same speech label an alert
# sentence would use conversationally, since this app has never maintained a
# separate translated alert register. The tier emoji at the front of the
# sentence is decoration enough that the label itself doesn't need one.
HAZARD_LABELS_BY_LANG = {lang: alert_labels_by_lang(lang) for lang in SUPPORTED_LANGS}
HAZARD_LABELS = HAZARD_LABELS_BY_LANG["en"]

TIER_EMOJI = {"advisory": "\U0001F535", "watch": "\U0001F7E1", "warning": "\U0001F534"}

# Tier names are kept in English across every language variant — they're
# already the vocabulary NDMA/disaster-alert SMS in India actually use, and
# translating them risks inventing terminology nobody recognizes (the same
# caution ingest/report_conversation.py already applies to hazard names).
_TIER_SENTENCE = {
    "en": "{emoji} {tier}: {label} reported near your area ({n} report{plural}).",
    "ta": "{emoji} {tier}: உங்கள் பகுதிக்கு அருகில் {label} தெரிவிக்கப்பட்டுள்ளது ({n} அறிக்கை{plural}).",
    "te": "{emoji} {tier}: మీ ప్రాంతానికి సమీపంలో {label} నివేదించబడింది ({n} నివేదిక{plural}).",
}
_TIER_SENTENCE_SHORT = {
    "en": "{tier}: {label} near you ({n} report{plural}).",
    "ta": "{tier}: {label} அருகில் ({n}).",
    "te": "{tier}: {label} సమీపంలో ({n}).",
}
assert set(_TIER_SENTENCE) == set(_TIER_SENTENCE_SHORT) == set(SUPPORTED_LANGS)


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
    """Per-tier, per-language, per-channel-length alert text (phase 3,
    milestone 4). Every language gets both a "standard" variant (full
    wording) and a "short" one (SMS-safe, no emoji) — see `message_text()` for
    how a channel/language picks between them at send time. An analyst's
    optional warning-tier note is free text (can't be auto-translated without
    an LLM call this path deliberately avoids, to keep automatic/analyst
    alert issuance fast and deterministic) so it's appended verbatim in
    English only, same limitation Tamil/Telugu callers already accept
    elsewhere in this app.
    """
    n = report_count
    plural_en = "" if n == 1 else "s"  # only English's sentence has a plural slot
    out = {}
    for lang in SUPPORTED_LANGS:
        label = HAZARD_LABELS_BY_LANG[lang].get(hazard_type) or hazard_type.replace("_", " ").title()
        plural = plural_en if lang == "en" else ""
        standard = _TIER_SENTENCE[lang].format(
            emoji=TIER_EMOJI[tier], tier=tier.upper(), label=label, n=n, plural=plural
        )
        short = _TIER_SENTENCE_SHORT[lang].format(tier=tier.upper(), label=label, n=n, plural=plural)
        if note and lang == "en":
            standard += f" {note}"
        out[lang] = {"standard": standard, "short": short}
    return out


def message_text(message: dict, lang: str = "en", channel: str = "push") -> str:
    """Resolve one displayable string from a per-language/per-channel-length
    message dict (see `draft_message`). "short" is picked for sms/whatsapp
    (character-constrained channels), "standard" otherwise. Falls back to
    English, then to whatever variant is actually present — and transparently
    handles alert rows created before this milestone, whose `message[lang]`
    value is still a flat string rather than a variant dict."""
    variants = message.get(lang) or message.get("en") or {}
    if isinstance(variants, str):
        return variants
    key = "short" if channel in ("sms", "whatsapp") else "standard"
    return variants.get(key) or variants.get("standard") or next(iter(variants.values()), "")
