"""Meta WhatsApp Business Cloud API client — plain httpx, no SDK dependency,
same style as every other real adapter in this app (delivery/adapters.py,
chat/llm.py). Every function is gated on whatsapp_access_token/
whatsapp_phone_number_id and degrades to a logged no-op (never raises) when
unconfigured, so the inbound webhook can still be exercised end-to-end
(session transitions, create_report) even without a Meta account — outbound
sends just silently no-op, same credential-gated-degrade pattern used
everywhere else in this codebase.
"""
import hashlib
import hmac
import logging

import httpx

from app.core.config import get_settings
from app.modules.ingest import report_conversation as conv

log = logging.getLogger(__name__)

# Meta's WhatsApp list-message limits: max 24 chars per row title, 72 per
# row description, 10 rows total across all sections.
_ROW_TITLE_MAX = 24
_ROW_DESC_MAX = 72


def _configured(settings) -> bool:
    return bool(settings.whatsapp_access_token and settings.whatsapp_phone_number_id)


def _base_url(settings) -> str:
    return f"https://graph.facebook.com/{settings.whatsapp_api_version}/{settings.whatsapp_phone_number_id}"


def _headers(settings) -> dict:
    return {"Authorization": f"Bearer {settings.whatsapp_access_token}", "Content-Type": "application/json"}


def send_text(to: str, body: str) -> bool:
    settings = get_settings()
    if not _configured(settings):
        log.info("WhatsApp send skipped (not configured): to=%s", to)
        return False
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}}
    try:
        resp = httpx.post(f"{_base_url(settings)}/messages", headers=_headers(settings), json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception:
        log.exception("WhatsApp send_text failed: to=%s", to)
        return False


def send_hazard_menu(to: str, body_text: str) -> bool:
    settings = get_settings()
    if not _configured(settings):
        log.info("WhatsApp send skipped (not configured): to=%s", to)
        return False
    rows = []
    for hazard_type, label in conv.hazard_menu_items():
        title = conv.HAZARD_SPEECH_LABELS[hazard_type].capitalize()[:_ROW_TITLE_MAX]
        rows.append({"id": f"hz:{hazard_type}", "title": title, "description": label[:_ROW_DESC_MAX]})
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body_text},
            "action": {"button": "Choose hazard", "sections": [{"title": "Hazard type", "rows": rows}]},
        },
    }
    try:
        resp = httpx.post(f"{_base_url(settings)}/messages", headers=_headers(settings), json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception:
        log.exception("WhatsApp send_hazard_menu failed: to=%s", to)
        return False


def download_media(media_id: str) -> bytes | None:
    settings = get_settings()
    if not _configured(settings):
        log.info("WhatsApp media download skipped (not configured): media_id=%s", media_id)
        return None
    try:
        meta_resp = httpx.get(
            f"https://graph.facebook.com/{settings.whatsapp_api_version}/{media_id}",
            headers=_headers(settings),
            timeout=10,
        )
        meta_resp.raise_for_status()
        media_url = meta_resp.json()["url"]
        data_resp = httpx.get(media_url, headers=_headers(settings), timeout=30)
        data_resp.raise_for_status()
        return data_resp.content
    except Exception:
        log.exception("WhatsApp media download failed: media_id=%s", media_id)
        return None


def verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """HMAC-SHA256 over the raw request body, keyed by the Meta app secret,
    per Meta's webhook security requirements. If no app secret is configured
    (local/dev, no real Meta app to copy a secret from), verification is
    skipped rather than rejecting every request — same posture as running
    without any of the other gated credentials in this app."""
    settings = get_settings()
    if not settings.whatsapp_app_secret:
        log.warning("whatsapp_app_secret not configured; skipping webhook signature verification")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(settings.whatsapp_app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))
