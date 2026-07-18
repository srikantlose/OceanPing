import pytest

from app.modules.ingest import report_conversation as conv
from app.models import HAZARD_TYPES


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


class BrokenRedis:
    def get(self, *a, **k):
        raise ConnectionError("redis is down")

    def set(self, *a, **k):
        raise ConnectionError("redis is down")

    def delete(self, *a, **k):
        raise ConnectionError("redis is down")


# --- hazard menu -------------------------------------------------------------

def test_hazard_menu_items_matches_canonical_hazard_order():
    assert [hz for hz, _label in conv.hazard_menu_items()] == HAZARD_TYPES


def test_hazard_speech_labels_cover_every_hazard_type():
    assert set(conv.HAZARD_SPEECH_LABELS) == set(HAZARD_TYPES)


# --- localization (phase 2, milestone 5) --------------------------------------

def test_hazard_labels_by_lang_cover_every_supported_lang_and_hazard():
    for lang in conv.SUPPORTED_LANGS:
        assert set(conv.HAZARD_LABELS_BY_LANG[lang]) == set(HAZARD_TYPES)
        assert set(conv.HAZARD_SPEECH_LABELS_BY_LANG[lang]) == set(HAZARD_TYPES)


def test_normalize_lang_falls_back_to_english():
    assert conv.normalize_lang("ta") == "ta"
    assert conv.normalize_lang("ta-IN") == "ta"
    assert conv.normalize_lang("TE") == "te"
    assert conv.normalize_lang("fr") == "en"
    assert conv.normalize_lang(None) == "en"


def test_hazard_menu_items_lang_returns_localized_labels_in_canonical_order():
    items = conv.hazard_menu_items("ta")
    assert [hz for hz, _label in items] == HAZARD_TYPES
    assert dict(items)["oil_spill"] == conv.HAZARD_LABELS_BY_LANG["ta"]["oil_spill"]


def test_start_with_unsupported_lang_falls_back_to_english():
    session, prompt = conv.start(lang="fr")
    assert session.lang == "en"
    assert prompt == conv.PROMPTS_BY_LANG["en"]["location"]


# --- pure state transitions ---------------------------------------------------

def test_start_returns_location_state():
    session, prompt = conv.start()
    assert session.state == conv.ConvState.LOCATION
    assert "location" in prompt.lower()


def test_full_happy_path_transitions():
    session, _ = conv.start()
    session, prompt = conv.on_location(session, 13.05, 80.28)
    assert session.state == conv.ConvState.HAZARD
    assert session.lat == 13.05 and session.lon == 80.28

    session, prompt = conv.on_hazard(session, "oil_spill")
    assert session.state == conv.ConvState.DESCRIPTION
    assert session.hazard_type == "oil_spill"
    assert "oil spill" in prompt.lower() or "Oil spill" in prompt

    session, prompt = conv.on_description(session, "black slick near the shore")
    assert session.state == conv.ConvState.PHOTO
    assert session.text == "black slick near the shore"

    session = conv.mark_done(session)
    assert session.state == conv.ConvState.DONE

    kwargs = conv.build_report_kwargs(session)
    assert kwargs == {"lat": 13.05, "lon": 80.28, "hazard_type": "oil_spill", "text": "black slick near the shore"}


def test_full_happy_path_transitions_in_tamil():
    session, prompt = conv.start(lang="ta")
    assert session.lang == "ta"
    assert prompt == conv.PROMPTS_BY_LANG["ta"]["location"]

    session, prompt = conv.on_location(session, 13.05, 80.28)
    assert prompt == conv.PROMPTS_BY_LANG["ta"]["hazard"]

    session, prompt = conv.on_hazard(session, "oil_spill")
    assert conv.HAZARD_LABELS_BY_LANG["ta"]["oil_spill"] in prompt
    assert conv.PROMPTS_BY_LANG["ta"]["description"] in prompt

    session, prompt = conv.on_description(session, "black slick near the shore")
    assert prompt == conv.PROMPTS_BY_LANG["ta"]["photo"]

    kwargs = conv.build_report_kwargs(conv.mark_done(session))
    assert kwargs["hazard_type"] == "oil_spill"


def test_skip_description_stores_none_text():
    session, _ = conv.start()
    session, _ = conv.on_location(session, 1.0, 2.0)
    session, _ = conv.on_hazard(session, "erosion")
    session, prompt = conv.skip_description(session)
    assert session.state == conv.ConvState.PHOTO
    assert session.text is None
    assert "photo" in prompt.lower()


def test_on_location_wrong_state_raises():
    session, _ = conv.start()
    session, _ = conv.on_location(session, 1.0, 2.0)  # now in HAZARD state
    with pytest.raises(conv.ConversationError):
        conv.on_location(session, 3.0, 4.0)


def test_on_hazard_rejects_unknown_hazard_type():
    session, _ = conv.start()
    session, _ = conv.on_location(session, 1.0, 2.0)
    with pytest.raises(conv.ConversationError):
        conv.on_hazard(session, "not_a_real_hazard")


def test_mark_done_requires_photo_state():
    session, _ = conv.start()
    with pytest.raises(conv.ConversationError):
        conv.mark_done(session)


# --- session (de)serialization ------------------------------------------------

def test_session_json_roundtrip():
    session, _ = conv.start()
    session, _ = conv.on_location(session, 13.05, 80.28)
    session, _ = conv.on_hazard(session, "tsunami")
    restored = conv.ReportSession.from_json(session.to_json())
    assert restored == session


def test_session_from_json_defaults_lang_for_pre_milestone5_sessions():
    """A session serialized before milestone 5 has no "lang" key at all —
    from_json must still load it and default to English rather than raise."""
    import json

    raw = json.dumps({"state": "hazard", "lat": 1.0, "lon": 2.0, "hazard_type": None, "text": None})
    session = conv.ReportSession.from_json(raw)
    assert session.lang == "en"


# --- Redis-backed session store ----------------------------------------------

def test_save_load_clear_session_roundtrip(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(conv, "get_redis", lambda: fake)

    assert conv.load_session("whatsapp", "+91999") is None

    session, _ = conv.start()
    session, _ = conv.on_location(session, 10.0, 20.0)
    conv.save_session("whatsapp", "+91999", session)

    loaded = conv.load_session("whatsapp", "+91999")
    assert loaded == session

    conv.clear_session("whatsapp", "+91999")
    assert conv.load_session("whatsapp", "+91999") is None


def test_session_store_degrades_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr(conv, "get_redis", lambda: BrokenRedis())
    session, _ = conv.start()
    conv.save_session("whatsapp", "+91999", session)  # must not raise
    assert conv.load_session("whatsapp", "+91999") is None  # must not raise
    conv.clear_session("whatsapp", "+91999")  # must not raise
