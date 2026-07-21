import inspect

from app.modules.alerts import engine


def test_tier_rank_order():
    assert engine.TIER_RANK["advisory"] < engine.TIER_RANK["watch"] < engine.TIER_RANK["warning"]


def test_unverified_incident_gets_no_tier():
    assert engine.eligible_tier("unverified", 10, 1.0, 3) is None


def test_corroborated_with_no_instrument_is_advisory_only():
    assert engine.eligible_tier("corroborated", 10, 0.0, 3) == "advisory"


def test_verified_status_is_at_least_advisory():
    assert engine.eligible_tier("verified", 0, 0.0, 3) == "advisory"


def test_watch_requires_both_instrument_and_reporter_threshold():
    assert engine.eligible_tier("corroborated", 2, 0.8, 3) == "advisory"  # too few reporters
    assert engine.eligible_tier("corroborated", 3, 0.0, 3) == "advisory"  # no instrument
    assert engine.eligible_tier("corroborated", 3, 0.8, 3) == "watch"


def test_eligible_tier_can_never_return_warning():
    """Structural guarantee behind the no-citizen-only-escalation rule: no
    combination of automatic signals may produce a warning-tier result."""
    for status in ("unverified", "corroborated", "verified", "rejected", "bogus"):
        for n in (0, 1, 3, 5, 50, 10_000):
            for instrument in (0.0, 0.01, 0.5, 1.0):
                assert engine.eligible_tier(status, n, instrument, 3) != "warning"


def test_eligible_tier_signature_has_no_analyst_parameter():
    """The function must be structurally incapable of attributing an
    analyst — if someone adds an `analyst` kwarg, warning becomes reachable."""
    params = set(inspect.signature(engine.eligible_tier).parameters)
    assert "analyst" not in params
    assert "issued_by" not in params


def test_draft_message_contains_tier_and_report_count():
    msg = engine.draft_message("coastal_flooding", "watch", 5)
    assert "WATCH" in msg["en"]["standard"]
    assert "5 reports" in msg["en"]["standard"]
    assert "Coastal flooding" in msg["en"]["standard"]


def test_draft_message_singular_report_count():
    msg = engine.draft_message("high_waves", "advisory", 1)
    standard = msg["en"]["standard"]
    assert "1 report " in standard or standard.endswith("1 report).")


def test_draft_message_appends_note():
    msg = engine.draft_message("oil_spill", "warning", 2, note="Confirmed via satellite.")
    assert "Confirmed via satellite." in msg["en"]["standard"]


def test_draft_message_unknown_hazard_falls_back_to_title_case():
    msg = engine.draft_message("weird_new_hazard", "advisory", 1)
    assert "Weird New Hazard" in msg["en"]["standard"]


def test_draft_message_covers_every_supported_language():
    msg = engine.draft_message("tsunami", "warning", 3)
    assert set(msg) == {"en", "ta", "te"}
    for lang in msg:
        assert msg[lang]["standard"]
        assert msg[lang]["short"]


def test_draft_message_short_variant_has_no_emoji():
    msg = engine.draft_message("tsunami", "warning", 3)
    assert engine.TIER_EMOJI["warning"] not in msg["en"]["short"]
    assert engine.TIER_EMOJI["warning"] in msg["en"]["standard"]


def test_message_text_picks_short_for_sms_and_whatsapp():
    msg = engine.draft_message("tsunami", "watch", 2)
    assert engine.message_text(msg, "en", "sms") == msg["en"]["short"]
    assert engine.message_text(msg, "en", "whatsapp") == msg["en"]["short"]
    assert engine.message_text(msg, "en", "telegram") == msg["en"]["standard"]
    assert engine.message_text(msg, "en", "web_push") == msg["en"]["standard"]


def test_message_text_falls_back_to_english_for_unknown_language():
    msg = engine.draft_message("tsunami", "watch", 2)
    assert engine.message_text(msg, "fr", "telegram") == msg["en"]["standard"]


def test_message_text_handles_pre_milestone4_flat_string_rows():
    """Alert rows created before per-channel-length variants existed still
    have message["en"] as a plain string, not a variant dict."""
    legacy = {"en": "Watch: coastal flooding (3 reports)."}
    assert engine.message_text(legacy, "en", "sms") == legacy["en"]
    assert engine.message_text(legacy, "ta", "telegram") == legacy["en"]
