"""Hazard registry (phase 4, milestone 2): config-driven per-hazard behavior.

Two layers, matching this project's established split between unit and live
verification: the parity tests below prove the refactor changed *nothing*
about what every existing hazard means (same instrument variables, satellite
recipes, labels in all three languages, CAP keywords, FAQ content) — the
actual regression risk in a "move nine hand-written dicts into YAML" change.
The loader/fallback tests then prove the registry handles a new, minimal
hazard definition gracefully, which is the load-bearing claim for "adding a
hazard is a config PR" — end-to-end proof against the real running stack is
`scripts/hazard_registry_live_check.py`.
"""
import logging

import pytest

from app.modules.hazards import registry
from app.modules.hazards.registry import DEFINITIONS_DIR, HazardDef, load_registry


# --- parity: the refactor must not change any existing hazard's meaning -----

def test_hazard_order_matches_pre_refactor_order():
    assert registry.HAZARD_TYPES == [
        "coastal_flooding", "storm_surge", "high_waves", "tsunami", "rip_current",
        "oil_spill", "algal_bloom", "erosion", "other",
    ]


def test_instrument_variables_match_pre_refactor_table():
    assert registry.instrument_variables_table() == {
        "coastal_flooding": {"water_level", "wave_height", "air_pressure"},
        "storm_surge": {"water_level", "wave_height", "air_pressure"},
        "high_waves": {"wave_height", "water_level"},
        "tsunami": {"water_level", "wave_height"},
        "rip_current": {"wave_height"},
        "oil_spill": set(),
        "algal_bloom": set(),
        "erosion": {"wave_height", "water_level"},
        "other": {"water_level", "wave_height", "air_pressure"},
    }


def test_satellite_recipes_match_pre_refactor_table():
    assert registry.satellite_recipes_table() == {
        "oil_spill": "sentinel1_sar_dark_slick",
        "algal_bloom": "sentinel2_ndci_anomaly",
        "coastal_flooding": "sentinel1_water_extent",
        "storm_surge": "sentinel1_water_extent",
    }


def test_alert_labels_en_match_pre_refactor_table():
    assert registry.alert_labels_by_lang("en") == {
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


def test_menu_labels_en_match_pre_refactor_table():
    assert registry.menu_labels_by_lang("en") == {
        "coastal_flooding": "🌊 Coastal flooding",
        "storm_surge": "🌀 Storm surge",
        "high_waves": "🌊 High waves",
        "tsunami": "⚠️ Tsunami signs",
        "rip_current": "🏊 Rip current",
        "oil_spill": "🛢️ Oil spill",
        "algal_bloom": "🟢 Algal bloom / fish kill",
        "erosion": "🏖️ Coastal erosion",
        "other": "❓ Other",
    }


def test_speech_labels_en_match_pre_refactor_table():
    assert registry.speech_labels_by_lang("en") == {
        "coastal_flooding": "coastal flooding",
        "storm_surge": "storm surge",
        "high_waves": "high waves",
        "tsunami": "tsunami signs",
        "rip_current": "rip current",
        "oil_spill": "oil spill",
        "algal_bloom": "algal bloom or fish kill",
        "erosion": "coastal erosion",
        "other": "another hazard",
    }


def test_tamil_and_telugu_labels_are_present_for_every_hazard():
    # Content fidelity is covered by test_report_conversation.py's existing
    # tests (which now read through this registry); this just confirms the
    # registry itself still has full ta/te coverage for every shipped hazard
    # rather than silently falling back to English anywhere.
    for lang in ("ta", "te"):
        for hazard in registry.HAZARD_TYPES:
            hz = registry.HAZARDS[hazard]
            assert lang in hz.menu_label, f"{hazard} missing {lang} menu_label"
            assert lang in hz.speech_label, f"{hazard} missing {lang} speech_label"


def test_cap_event_keywords_reproduce_pre_refactor_mapping():
    from app.modules.alerts.cap_ingest import map_event_to_hazard

    expected = {
        "Tsunami Warning": "tsunami",
        "Storm Surge Warning": "storm_surge",
        "Storm Tide Advisory": "storm_surge",
        "High Wave Alert": "high_waves",
        "High Surf Advisory": "high_waves",
        "Rip Current Statement": "rip_current",
        "Coastal Flood Warning": "coastal_flooding",
        "Oil Spill Notice": "oil_spill",
        "Red Tide Advisory": "algal_bloom",
        "Algal Bloom Warning": "algal_bloom",
        "Coastal Erosion Notice": "erosion",
        "Volcanic Ashfall Warning": None,  # no keyword matches — stays unmapped
    }
    for event_text, hazard in expected.items():
        assert map_event_to_hazard(event_text) == hazard


def test_faq_entries_preserve_original_ids_and_hazard_coverage():
    entries = registry.faq_entries()
    ids = {e["id"] for e in entries}
    assert ids == {
        "faq-tsunami-signs", "faq-rip-current-safety", "faq-oil-spill-report",
        "faq-algal-bloom", "faq-coastal-flooding-general", "faq-erosion",
    }
    for entry in entries:
        assert entry["title"].strip()
        assert entry["content"].strip()


# --- loader behavior: duplicate/missing-field handling ----------------------

def test_load_registry_orders_by_the_order_field_not_filename(tmp_path):
    (tmp_path / "z_first.yaml").write_text(
        "key: z_hazard\norder: 1\nmenu_label:\n  en: Z\n", encoding="utf-8"
    )
    (tmp_path / "a_second.yaml").write_text(
        "key: a_hazard\norder: 2\nmenu_label:\n  en: A\n", encoding="utf-8"
    )
    hazards = load_registry(tmp_path)
    assert list(hazards.keys()) == ["z_hazard", "a_hazard"]


def test_load_registry_rejects_duplicate_keys(tmp_path):
    (tmp_path / "one.yaml").write_text(
        "key: dup\norder: 1\nmenu_label:\n  en: One\n", encoding="utf-8"
    )
    (tmp_path / "two.yaml").write_text(
        "key: dup\norder: 2\nmenu_label:\n  en: Two\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="duplicate hazard key"):
        load_registry(tmp_path)


def test_load_registry_requires_a_key_field(tmp_path):
    (tmp_path / "broken.yaml").write_text("order: 1\nmenu_label:\n  en: Nameless\n", encoding="utf-8")
    with pytest.raises(ValueError, match="key"):
        load_registry(tmp_path)


# --- the actual "config PR adds a hazard" claim -----------------------------

def test_a_minimal_hazard_needs_only_key_and_english_menu_label(tmp_path, caplog):
    """The bar the plan itself sets: a new hazard with nothing but the
    required fields must load, fall back to English everywhere a translation
    or optional table is missing, and never crash a consumer."""
    (tmp_path / "king_tide.yaml").write_text(
        "key: king_tide\norder: 15\nmenu_label:\n  en: \"King tide\"\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        hazards = load_registry(tmp_path)

    hz = hazards["king_tide"]
    assert hz.label("en") == "King tide"
    assert hz.label("ta") == "King tide"  # falls back to English
    assert hz.label("te") == "King tide"
    assert hz.speech("ta") == "King tide"
    assert hz.alert("en") == "King tide"  # alert_label_en defaults to the menu label
    assert hz.alert("ta") == "King tide"
    assert hz.instrument_variables == set()
    assert hz.satellite_recipe is None
    assert hz.cap_event_keywords == []
    assert hz.faq == []
    assert "king_tide" in caplog.text and "ta" in caplog.text  # warned, didn't just fail silently


def test_a_toy_hazard_flows_through_every_derived_table_alongside_the_real_ones(tmp_path, monkeypatch):
    """Simulates what adding definitions/king_tide.yaml to the real directory
    and restarting the process would produce: every table each consumer
    module builds from the registry picks up the new hazard automatically,
    with zero changes to scoring/satellite/alerts/CAP code — see
    scripts/hazard_registry_live_check.py for the same claim proven against
    the real running API instead of these derivation functions directly."""
    for name in ("tsunami", "oil_spill", "other"):
        src = (DEFINITIONS_DIR / f"{name}.yaml").read_text(encoding="utf-8")
        (tmp_path / f"{name}.yaml").write_text(src, encoding="utf-8")
    (tmp_path / "king_tide.yaml").write_text(
        "key: king_tide\n"
        "order: 15\n"
        "menu_label:\n  en: \"King tide\"\n"
        "instrument_variables: [water_level]\n",
        encoding="utf-8",
    )
    hazards = load_registry(tmp_path)
    monkeypatch.setattr(registry, "HAZARDS", hazards)
    monkeypatch.setattr(registry, "HAZARD_TYPES", list(hazards.keys()))

    assert "king_tide" in registry.HAZARD_TYPES
    assert registry.instrument_variables_table()["king_tide"] == {"water_level"}
    assert "king_tide" not in registry.satellite_recipes_table()
    assert all(hz != "king_tide" for _kw, hz in registry.cap_event_keywords_table())
    assert all(e["id"] != "king_tide" for e in registry.faq_entries())
    # the real hazards alongside it are completely unaffected
    assert registry.instrument_variables_table()["tsunami"] == {"water_level", "wave_height"}
    assert registry.satellite_recipes_table()["oil_spill"] == "sentinel1_sar_dark_slick"
