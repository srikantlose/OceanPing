import hashlib
import hmac
from types import SimpleNamespace

from app.modules.whatsapp import client


def _settings(**overrides):
    base = dict(
        whatsapp_access_token="",
        whatsapp_phone_number_id="",
        whatsapp_api_version="v20.0",
        whatsapp_app_secret="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _OKResponse:
    def __init__(self, json_data=None, content=b""):
        self._json_data = json_data or {}
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_data


# ---------- send_text ----------

def test_send_text_skips_without_credentials(monkeypatch):
    monkeypatch.setattr(client, "get_settings", lambda: _settings())
    assert client.send_text("+91999", "hello") is False


def test_send_text_posts_expected_payload(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json)
        return _OKResponse()

    monkeypatch.setattr(client.httpx, "post", fake_post)
    monkeypatch.setattr(
        client, "get_settings",
        lambda: _settings(whatsapp_access_token="tok", whatsapp_phone_number_id="123"),
    )
    assert client.send_text("+91999", "hello") is True
    assert "123" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer tok"
    assert captured["json"]["to"] == "+91999"
    assert captured["json"]["text"]["body"] == "hello"


def test_send_text_returns_false_on_http_error(monkeypatch):
    def fake_post(*a, **k):
        raise ConnectionError("boom")

    monkeypatch.setattr(client.httpx, "post", fake_post)
    monkeypatch.setattr(
        client, "get_settings",
        lambda: _settings(whatsapp_access_token="tok", whatsapp_phone_number_id="123"),
    )
    assert client.send_text("+91999", "hello") is False


# ---------- send_hazard_menu ----------

def test_send_hazard_menu_builds_one_row_per_hazard_within_limits(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _OKResponse()

    monkeypatch.setattr(client.httpx, "post", fake_post)
    monkeypatch.setattr(
        client, "get_settings",
        lambda: _settings(whatsapp_access_token="tok", whatsapp_phone_number_id="123"),
    )
    assert client.send_hazard_menu("+91999", "What do you see?") is True
    rows = captured["json"]["interactive"]["action"]["sections"][0]["rows"]
    assert len(rows) == 9
    assert all(row["id"].startswith("hz:") for row in rows)
    assert all(len(row["title"]) <= 24 for row in rows)
    assert all(len(row["description"]) <= 72 for row in rows)


def test_send_hazard_menu_skips_without_credentials(monkeypatch):
    monkeypatch.setattr(client, "get_settings", lambda: _settings())
    assert client.send_hazard_menu("+91999", "What do you see?") is False


# ---------- download_media ----------

def test_download_media_skips_without_credentials(monkeypatch):
    monkeypatch.setattr(client, "get_settings", lambda: _settings())
    assert client.download_media("media-1") is None


def test_download_media_fetches_url_then_bytes(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        if "graph.facebook.com" in url:
            return _OKResponse(json_data={"url": "https://cdn.example.com/file.jpg"})
        return _OKResponse(content=b"binarydata")

    monkeypatch.setattr(client.httpx, "get", fake_get)
    monkeypatch.setattr(
        client, "get_settings",
        lambda: _settings(whatsapp_access_token="tok", whatsapp_phone_number_id="123"),
    )
    result = client.download_media("media-1")
    assert result == b"binarydata"
    assert any("media-1" in c for c in calls)
    assert any(c == "https://cdn.example.com/file.jpg" for c in calls)


def test_download_media_returns_none_on_error(monkeypatch):
    def fake_get(*a, **k):
        raise ConnectionError("boom")

    monkeypatch.setattr(client.httpx, "get", fake_get)
    monkeypatch.setattr(
        client, "get_settings",
        lambda: _settings(whatsapp_access_token="tok", whatsapp_phone_number_id="123"),
    )
    assert client.download_media("media-1") is None


# ---------- verify_signature ----------

def test_verify_signature_skips_when_app_secret_unconfigured(monkeypatch):
    monkeypatch.setattr(client, "get_settings", lambda: _settings())
    assert client.verify_signature(b"any body", None) is True


def test_verify_signature_accepts_valid_signature(monkeypatch):
    monkeypatch.setattr(client, "get_settings", lambda: _settings(whatsapp_app_secret="s3cret"))
    body = b'{"hello": "world"}'
    digest = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert client.verify_signature(body, f"sha256={digest}") is True


def test_verify_signature_rejects_invalid_signature(monkeypatch):
    monkeypatch.setattr(client, "get_settings", lambda: _settings(whatsapp_app_secret="s3cret"))
    assert client.verify_signature(b'{"hello": "world"}', "sha256=deadbeef") is False


def test_verify_signature_rejects_missing_header(monkeypatch):
    monkeypatch.setattr(client, "get_settings", lambda: _settings(whatsapp_app_secret="s3cret"))
    assert client.verify_signature(b'{"hello": "world"}', None) is False
