from types import SimpleNamespace

from app.modules.ingest import report_conversation as conv
from app.modules.whatsapp import service


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


def _text_message(from_number, body):
    return {"from": from_number, "id": "wamid.1", "type": "text", "text": {"body": body}}


def _location_message(from_number, lat, lon):
    return {"from": from_number, "id": "wamid.2", "type": "location", "location": {"latitude": lat, "longitude": lon}}


def _hazard_message(from_number, hazard_type):
    return {
        "from": from_number, "id": "wamid.3", "type": "interactive",
        "interactive": {"type": "list_reply", "list_reply": {"id": f"hz:{hazard_type}", "title": "x"}},
    }


def _image_message(from_number, media_id="media-1"):
    return {"from": from_number, "id": "wamid.4", "type": "image", "image": {"id": media_id, "mime_type": "image/jpeg"}}


def _payload(*messages):
    return {"entry": [{"changes": [{"value": {"messages": list(messages)}}]}]}


def _patch_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(conv, "get_redis", lambda: fake)
    return fake


def _patch_sends(monkeypatch):
    sent = {"text": [], "menu": []}
    monkeypatch.setattr(service.client, "send_text", lambda to, body: sent["text"].append((to, body)) or True)
    monkeypatch.setattr(service.client, "send_hazard_menu", lambda to, body: sent["menu"].append((to, body)) or True)
    return sent


# --- session bootstrap --------------------------------------------------------

def test_trigger_word_starts_session(monkeypatch):
    _patch_redis(monkeypatch)
    sent = _patch_sends(monkeypatch)
    service.handle_payload(object(), _payload(_text_message("+91999", "report")))

    session = conv.load_session(service.CHANNEL, "+91999")
    assert session is not None
    assert session.state == conv.ConvState.LOCATION
    assert sent["text"]  # location prompt sent


def test_no_trigger_word_sends_help_and_creates_no_session(monkeypatch):
    _patch_redis(monkeypatch)
    sent = _patch_sends(monkeypatch)
    service.handle_payload(object(), _payload(_text_message("+91999", "hi there")))

    assert conv.load_session(service.CHANNEL, "+91999") is None
    assert sent["text"] == [("+91999", service.HELP_TEXT)]


def test_cancel_clears_an_active_session(monkeypatch):
    fake = _patch_redis(monkeypatch)
    _patch_sends(monkeypatch)
    session, _ = conv.start()
    conv.save_session(service.CHANNEL, "+91999", session)

    service.handle_payload(object(), _payload(_text_message("+91999", "cancel")))
    assert conv.load_session(service.CHANNEL, "+91999") is None


def test_wrong_message_type_for_state_reprompts_without_advancing(monkeypatch):
    _patch_redis(monkeypatch)
    sent = _patch_sends(monkeypatch)
    session, _ = conv.start()
    conv.save_session(service.CHANNEL, "+91999", session)

    service.handle_payload(object(), _payload(_text_message("+91999", "not a location")))
    assert conv.load_session(service.CHANNEL, "+91999").state == conv.ConvState.LOCATION
    assert sent["text"]


# --- full happy path -----------------------------------------------------------

def test_full_flow_creates_report_and_clears_session(monkeypatch):
    _patch_redis(monkeypatch)
    _patch_sends(monkeypatch)
    captured = {}

    class _FakeReport:
        id = "11111111-1111-1111-1111-111111111111"
        hazard_type = "oil_spill"

    def fake_create_report(db, **kwargs):
        captured.update(kwargs)
        return _FakeReport()

    monkeypatch.setattr(service, "create_report", fake_create_report)
    monkeypatch.setattr(service.client, "download_media", lambda media_id: b"jpeg-bytes")

    from_number = "+91999"
    service.handle_payload(object(), _payload(_text_message(from_number, "report")))
    service.handle_payload(object(), _payload(_location_message(from_number, 13.05, 80.28)))
    service.handle_payload(object(), _payload(_hazard_message(from_number, "oil_spill")))
    service.handle_payload(object(), _payload(_text_message(from_number, "black slick near the shore")))
    service.handle_payload(object(), _payload(_image_message(from_number)))

    assert captured["source"] == "whatsapp"
    assert captured["external_id"] == from_number
    assert captured["lat"] == 13.05 and captured["lon"] == 80.28
    assert captured["hazard_type"] == "oil_spill"
    assert captured["text"] == "black slick near the shore"
    assert captured["media_bytes"] == b"jpeg-bytes"
    assert conv.load_session(service.CHANNEL, from_number) is None


def test_skip_description_and_skip_photo_submits_without_text_or_media(monkeypatch):
    _patch_redis(monkeypatch)
    _patch_sends(monkeypatch)
    captured = {}

    class _FakeReport:
        id = "22222222-2222-2222-2222-222222222222"
        hazard_type = "erosion"

    monkeypatch.setattr(service, "create_report", lambda db, **kw: captured.update(kw) or _FakeReport())

    from_number = "+91888"
    service.handle_payload(object(), _payload(_text_message(from_number, "report")))
    service.handle_payload(object(), _payload(_location_message(from_number, 1.0, 2.0)))
    service.handle_payload(object(), _payload(_hazard_message(from_number, "erosion")))
    service.handle_payload(object(), _payload(_text_message(from_number, "skip")))
    service.handle_payload(object(), _payload(_text_message(from_number, "skip")))

    assert captured["text"] is None
    assert captured["media_bytes"] is None


def test_rate_limited_submission_clears_session_and_notifies(monkeypatch):
    _patch_redis(monkeypatch)
    sent = _patch_sends(monkeypatch)

    def raise_rate_limited(db, **kw):
        raise service.RateLimited("too many reports")

    monkeypatch.setattr(service, "create_report", raise_rate_limited)

    from_number = "+91777"
    service.handle_payload(object(), _payload(_text_message(from_number, "report")))
    service.handle_payload(object(), _payload(_location_message(from_number, 1.0, 2.0)))
    service.handle_payload(object(), _payload(_hazard_message(from_number, "erosion")))
    service.handle_payload(object(), _payload(_text_message(from_number, "skip")))
    service.handle_payload(object(), _payload(_text_message(from_number, "skip")))

    assert conv.load_session(service.CHANNEL, from_number) is None
    assert any("too many reports" in body for _to, body in sent["text"])
