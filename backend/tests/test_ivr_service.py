from types import SimpleNamespace

from app.modules.ivr import service


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


def _settings(**overrides):
    base = dict(ivr_recording_max_seconds=60, twilio_account_sid="", twilio_auth_token="")
    base.update(overrides)
    return SimpleNamespace(**base)


# --- handle_start --------------------------------------------------------------

def test_handle_start_gathers_a_digit_for_every_hazard():
    twiml = service.handle_start()
    assert "<Gather" in twiml
    assert 'action="/webhooks/ivr/voice?step=hazard"' in twiml
    for i in range(1, 10):
        assert f"Press {i} for" in twiml


# --- handle_hazard ---------------------------------------------------------------

def test_handle_hazard_valid_digit_stores_session_and_prompts_location(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(service, "get_redis", lambda: fake)
    twiml = service.handle_hazard("CA123", "6")  # 6th hazard_type = oil_spill
    assert "<Gather" in twiml
    assert 'action="/webhooks/ivr/voice?step=location"' in twiml
    session = service._load("CA123")
    assert session["hazard_type"] == "oil_spill"


def test_handle_hazard_invalid_digit_returns_error(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(service, "get_redis", lambda: fake)
    twiml = service.handle_hazard("CA123", "0")
    assert "Invalid selection" in twiml
    assert "<Gather" not in twiml


def test_handle_hazard_non_numeric_digit_returns_error(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(service, "get_redis", lambda: fake)
    twiml = service.handle_hazard("CA123", "")
    assert "Invalid selection" in twiml


# --- handle_location -------------------------------------------------------------

def test_handle_location_valid_digit_updates_session_and_prompts_recording(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(service, "get_redis", lambda: fake)
    monkeypatch.setattr(service, "get_settings", lambda: _settings())
    service._save("CA123", {"hazard_type": "oil_spill"})

    twiml = service.handle_location("CA123", "1")  # Marina Beach
    assert "<Record" in twiml
    assert 'action="/webhooks/ivr/voice?step=recording"' in twiml
    session = service._load("CA123")
    assert session["lat"] == 13.0500 and session["lon"] == 80.2824


def test_handle_location_invalid_digit_returns_error(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(service, "get_redis", lambda: fake)
    twiml = service.handle_location("CA123", "9")
    assert "Invalid selection" in twiml
    assert "<Record" not in twiml


# --- handle_recording ------------------------------------------------------------

def test_handle_recording_creates_report_with_transcript(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(service, "get_redis", lambda: fake)
    monkeypatch.setattr(service, "get_settings", lambda: _settings(twilio_account_sid="SID", twilio_auth_token="TOK"))
    service._save("CA123", {"hazard_type": "oil_spill", "lat": 13.05, "lon": 80.28})

    monkeypatch.setattr(service, "_download_recording", lambda url: b"audio-bytes")
    monkeypatch.setattr(service.voice, "transcribe", lambda audio: "black slick near the shore")

    captured = {}
    monkeypatch.setattr(service, "create_report", lambda db, **kw: captured.update(kw) or SimpleNamespace(id="r1"))

    twiml = service.handle_recording(object(), "CA123", "+91999", "https://api.twilio.com/recording1")
    assert "Thank you" in twiml
    assert captured["source"] == "ivr"
    assert captured["external_id"] == "+91999"
    assert captured["hazard_type"] == "oil_spill"
    assert captured["lat"] == 13.05 and captured["lon"] == 80.28
    assert captured["text"] == "black slick near the shore"
    assert service._load("CA123") == {}


def test_handle_recording_without_recording_url_submits_with_no_text(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(service, "get_redis", lambda: fake)
    monkeypatch.setattr(service, "get_settings", lambda: _settings())
    service._save("CA123", {"hazard_type": "erosion", "lat": 1.0, "lon": 2.0})

    captured = {}
    monkeypatch.setattr(service, "create_report", lambda db, **kw: captured.update(kw) or SimpleNamespace(id="r1"))

    twiml = service.handle_recording(object(), "CA123", "+91999", None)
    assert "Thank you" in twiml
    assert captured["text"] is None


def test_handle_recording_missing_session_returns_graceful_error(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(service, "get_redis", lambda: fake)
    twiml = service.handle_recording(object(), "unknown-call", "+91999", None)
    assert "went wrong" in twiml


def test_handle_recording_rate_limited(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(service, "get_redis", lambda: fake)
    monkeypatch.setattr(service, "get_settings", lambda: _settings())
    service._save("CA123", {"hazard_type": "erosion", "lat": 1.0, "lon": 2.0})

    from app.modules.ingest.service import RateLimited

    def raise_rate_limited(db, **kw):
        raise RateLimited("too many reports")

    monkeypatch.setattr(service, "create_report", raise_rate_limited)
    twiml = service.handle_recording(object(), "CA123", "+91999", None)
    assert "too many reports" in twiml
    assert service._load("CA123") == {}


def test_handle_recording_generic_failure_is_handled(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(service, "get_redis", lambda: fake)
    monkeypatch.setattr(service, "get_settings", lambda: _settings())
    service._save("CA123", {"hazard_type": "erosion", "lat": 1.0, "lon": 2.0})

    def raise_error(db, **kw):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(service, "create_report", raise_error)
    twiml = service.handle_recording(object(), "CA123", "+91999", None)
    assert "went wrong" in twiml


# --- _download_recording -----------------------------------------------------------

def test_download_recording_skips_without_twilio_credentials(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: _settings())
    assert service._download_recording("https://api.twilio.com/recording1") is None


def test_download_recording_fetches_mp3_with_basic_auth(monkeypatch):
    captured = {}

    class _Resp:
        content = b"mp3-bytes"

        def raise_for_status(self):
            pass

    def fake_get(url, auth=None, timeout=None):
        captured.update(url=url, auth=auth)
        return _Resp()

    monkeypatch.setattr(service.httpx, "get", fake_get)
    monkeypatch.setattr(service, "get_settings", lambda: _settings(twilio_account_sid="SID", twilio_auth_token="TOK"))
    result = service._download_recording("https://api.twilio.com/recording1")
    assert result == b"mp3-bytes"
    assert captured["url"] == "https://api.twilio.com/recording1.mp3"
    assert captured["auth"] == ("SID", "TOK")
