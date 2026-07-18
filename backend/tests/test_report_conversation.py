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
