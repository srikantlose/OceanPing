from types import SimpleNamespace

import httpx

from app.models import Alert, Subscription
from app.modules.delivery import adapters


def _alert(tier="watch"):
    return Alert(
        hazard_type="coastal_flooding",
        tier=tier,
        h3_cells=["cell1"],
        message={"en": "Watch: coastal flooding (3 reports)."},
        status="active",
        issued_by=None,
    )


def _sub(channel="telegram", address="12345", lang="en", meta=None):
    return Subscription(
        channel=channel, address=address, h3_cells=["cell1"], min_tier="advisory",
        lang=lang, meta=meta or {},
    )


class _OKResponse:
    def raise_for_status(self):
        pass


# ---------- Telegram ----------

def test_telegram_adapter_skips_without_token(monkeypatch):
    monkeypatch.setattr(adapters, "get_settings", lambda: SimpleNamespace(telegram_bot_token=""))
    result = adapters.TelegramAdapter().send(_alert(), _sub())
    assert result.status == "skipped"


def test_telegram_adapter_sends_with_expected_payload(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"], captured["json"] = url, json
        return _OKResponse()

    monkeypatch.setattr(adapters.httpx, "post", fake_post)
    monkeypatch.setattr(adapters, "get_settings", lambda: SimpleNamespace(telegram_bot_token="tok"))
    result = adapters.TelegramAdapter().send(_alert(), _sub(address="chat-1"))
    assert result.status == "sent"
    assert "tok" in captured["url"]
    assert captured["json"]["chat_id"] == "chat-1"


def test_telegram_adapter_reports_failure_without_raising(monkeypatch):
    def fake_post(*a, **k):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(adapters.httpx, "post", fake_post)
    monkeypatch.setattr(adapters, "get_settings", lambda: SimpleNamespace(telegram_bot_token="tok"))
    result = adapters.TelegramAdapter().send(_alert(), _sub())
    assert result.status == "failed"
    assert result.detail


# ---------- Web push ----------

def test_webpush_adapter_skips_without_vapid_key(monkeypatch):
    monkeypatch.setattr(adapters, "get_settings", lambda: SimpleNamespace(vapid_private_key=""))
    result = adapters.WebPushAdapter().send(_alert(), _sub(channel="web_push", meta={}))
    assert result.status == "skipped"


def test_webpush_adapter_fails_when_subscription_missing_push_info(monkeypatch):
    monkeypatch.setattr(
        adapters, "get_settings",
        lambda: SimpleNamespace(vapid_private_key="priv", vapid_admin_email="a@b.com"),
    )
    result = adapters.WebPushAdapter().send(_alert(), _sub(channel="web_push", meta={}))
    assert result.status == "failed"


# ---------- SMS: Console (default/local stub) ----------

def test_console_adapter_always_reports_sent():
    result = adapters.ConsoleAdapter().send(_alert(), _sub(channel="sms", address="+911234567890"))
    assert result.status == "sent"


# ---------- SMS: Twilio ----------

def test_twilio_adapter_skips_without_credentials(monkeypatch):
    monkeypatch.setattr(
        adapters, "get_settings",
        lambda: SimpleNamespace(twilio_account_sid="", twilio_auth_token="", twilio_from_number=""),
    )
    result = adapters.TwilioAdapter().send(_alert(), _sub(channel="sms", address="+91999"))
    assert result.status == "skipped"


def test_twilio_adapter_posts_expected_payload(monkeypatch):
    captured = {}

    def fake_post(url, data=None, auth=None, timeout=None):
        captured.update(url=url, data=data, auth=auth)
        return _OKResponse()

    monkeypatch.setattr(adapters.httpx, "post", fake_post)
    monkeypatch.setattr(
        adapters, "get_settings",
        lambda: SimpleNamespace(twilio_account_sid="SID", twilio_auth_token="TOK", twilio_from_number="+1000"),
    )
    result = adapters.TwilioAdapter().send(_alert(), _sub(channel="sms", address="+91999"))
    assert result.status == "sent"
    assert captured["data"]["To"] == "+91999"
    assert captured["data"]["From"] == "+1000"
    assert captured["auth"] == ("SID", "TOK")
    assert "SID" in captured["url"]


# ---------- SMS: Exotel ----------

def test_exotel_adapter_skips_without_credentials(monkeypatch):
    monkeypatch.setattr(
        adapters, "get_settings",
        lambda: SimpleNamespace(exotel_sid="", exotel_token="", exotel_from_number=""),
    )
    result = adapters.ExotelAdapter().send(_alert(), _sub(channel="sms", address="+91999"))
    assert result.status == "skipped"


def test_exotel_adapter_posts_expected_payload_with_basic_auth(monkeypatch):
    captured = {}

    def fake_post(url, data=None, auth=None, timeout=None):
        captured.update(url=url, data=data, auth=auth)
        return _OKResponse()

    monkeypatch.setattr(adapters.httpx, "post", fake_post)
    monkeypatch.setattr(
        adapters, "get_settings",
        lambda: SimpleNamespace(
            exotel_sid="SID", exotel_token="TOK", exotel_from_number="+1000",
            exotel_subdomain="api.exotel.com",
        ),
    )
    result = adapters.ExotelAdapter().send(_alert(), _sub(channel="sms", address="+91999"))
    assert result.status == "sent"
    assert captured["auth"] == ("SID", "TOK")
    assert "TOK" not in captured["url"]  # the secret token goes through auth=, never embedded in the URL
    assert captured["url"].startswith("https://api.exotel.com/")


# ---------- WhatsApp ----------

def test_whatsapp_adapter_skips_without_credentials(monkeypatch):
    monkeypatch.setattr(
        adapters, "get_settings",
        lambda: SimpleNamespace(whatsapp_access_token="", whatsapp_phone_number_id=""),
    )
    result = adapters.WhatsAppAdapter().send(_alert(), _sub(channel="whatsapp", address="+91999"))
    assert result.status == "skipped"


def test_whatsapp_adapter_posts_expected_payload(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json)
        return _OKResponse()

    monkeypatch.setattr(adapters.httpx, "post", fake_post)
    monkeypatch.setattr(
        adapters, "get_settings",
        lambda: SimpleNamespace(
            whatsapp_access_token="tok", whatsapp_phone_number_id="123", whatsapp_api_version="v20.0",
        ),
    )
    result = adapters.WhatsAppAdapter().send(_alert(), _sub(channel="whatsapp", address="+91999"))
    assert result.status == "sent"
    assert "123" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer tok"
    assert captured["json"]["to"] == "+91999"


def test_whatsapp_adapter_reports_failure_without_raising(monkeypatch):
    def fake_post(*a, **k):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(adapters.httpx, "post", fake_post)
    monkeypatch.setattr(
        adapters, "get_settings",
        lambda: SimpleNamespace(whatsapp_access_token="tok", whatsapp_phone_number_id="123", whatsapp_api_version="v20.0"),
    )
    result = adapters.WhatsAppAdapter().send(_alert(), _sub(channel="whatsapp", address="+91999"))
    assert result.status == "failed"
    assert result.detail


# ---------- Adapter factory ----------

def test_get_adapter_dispatches_telegram_web_push_and_whatsapp():
    assert isinstance(adapters.get_adapter("telegram"), adapters.TelegramAdapter)
    assert isinstance(adapters.get_adapter("web_push"), adapters.WebPushAdapter)
    assert isinstance(adapters.get_adapter("whatsapp"), adapters.WhatsAppAdapter)
    assert adapters.get_adapter("carrier_pigeon") is None


def test_get_adapter_picks_sms_provider_from_settings(monkeypatch):
    monkeypatch.setattr(adapters, "get_settings", lambda: SimpleNamespace(sms_provider="twilio"))
    assert isinstance(adapters.get_adapter("sms"), adapters.TwilioAdapter)

    monkeypatch.setattr(adapters, "get_settings", lambda: SimpleNamespace(sms_provider="exotel"))
    assert isinstance(adapters.get_adapter("sms"), adapters.ExotelAdapter)

    monkeypatch.setattr(adapters, "get_settings", lambda: SimpleNamespace(sms_provider="console"))
    assert isinstance(adapters.get_adapter("sms"), adapters.ConsoleAdapter)

    monkeypatch.setattr(adapters, "get_settings", lambda: SimpleNamespace(sms_provider="unknown-provider"))
    assert isinstance(adapters.get_adapter("sms"), adapters.ConsoleAdapter)  # safe default
