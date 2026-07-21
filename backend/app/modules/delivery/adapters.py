"""Channel adapters: a single `send(alert, subscription) -> DeliveryResult`
contract per channel, so the worker never needs to know Telegram/webpush/SMS
specifics. `get_adapter()` picks by `Subscription.channel`, with the SMS
provider chosen by config (`settings.sms_provider`) so switching from the
local Console stub to Twilio/Exotel is a config change, not a code change.
"""
import json
import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import get_settings
from app.models import Alert, Subscription
from app.modules.alerts.engine import message_text

log = logging.getLogger(__name__)


@dataclass
class DeliveryResult:
    status: str  # "sent" | "failed" | "skipped"
    detail: str | None = None


def _text_for(alert: Alert, subscription: Subscription) -> str:
    """Resolves per-language/per-channel-length text (see
    alerts/engine.py::message_text) — `alert` only needs a `.message` dict
    here, so this also accepts the narrative-correction shim
    modules/narratives/service.py passes in instead of a real Alert."""
    return message_text(alert.message, subscription.lang, subscription.channel)


class Adapter(Protocol):
    def send(self, alert: Alert, subscription: Subscription) -> DeliveryResult: ...


class TelegramAdapter:
    def send(self, alert: Alert, subscription: Subscription) -> DeliveryResult:
        token = get_settings().telegram_bot_token
        if not token:
            return DeliveryResult("skipped", "TELEGRAM_BOT_TOKEN not configured")
        try:
            resp = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": subscription.address, "text": _text_for(alert, subscription)},
                timeout=10,
            )
            resp.raise_for_status()
            return DeliveryResult("sent")
        except Exception as exc:
            return DeliveryResult("failed", str(exc)[:500])


class WebPushAdapter:
    def send(self, alert: Alert, subscription: Subscription) -> DeliveryResult:
        settings = get_settings()
        if not settings.vapid_private_key:
            return DeliveryResult("skipped", "VAPID keys not configured")
        push_info = (subscription.meta or {}).get("push_subscription")
        if not push_info:
            return DeliveryResult("failed", "subscription missing push_subscription meta")
        try:
            from pywebpush import WebPushException, webpush
        except ImportError:
            return DeliveryResult("failed", "pywebpush not installed")
        payload = json.dumps(
            {
                "title": f"OceanPing {alert.tier.upper()}",
                "body": _text_for(alert, subscription),
                "tier": alert.tier,
                "hazard_type": alert.hazard_type,
            }
        )
        try:
            webpush(
                subscription_info=push_info,
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": f"mailto:{settings.vapid_admin_email}"},
            )
            return DeliveryResult("sent")
        except WebPushException as exc:
            return DeliveryResult("failed", str(exc)[:500])
        except Exception as exc:
            return DeliveryResult("failed", str(exc)[:500])


class ConsoleAdapter:
    """Local/dev SMS stand-in — logs instead of paying a provider. Default
    when no SMS provider is configured, so the pipeline is exercisable without
    a Twilio/Exotel account."""

    def send(self, alert: Alert, subscription: Subscription) -> DeliveryResult:
        text = _text_for(alert, subscription)
        log.info("[console-sms] to=%s: %s", subscription.address, text)
        return DeliveryResult("sent", "console adapter — no real SMS sent")


class TwilioAdapter:
    def send(self, alert: Alert, subscription: Subscription) -> DeliveryResult:
        settings = get_settings()
        if not (settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_from_number):
            return DeliveryResult("skipped", "Twilio credentials not configured")
        url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
        try:
            resp = httpx.post(
                url,
                data={
                    "To": subscription.address,
                    "From": settings.twilio_from_number,
                    "Body": _text_for(alert, subscription),
                },
                auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                timeout=10,
            )
            resp.raise_for_status()
            return DeliveryResult("sent")
        except Exception as exc:
            return DeliveryResult("failed", str(exc)[:500])


class ExotelAdapter:
    def send(self, alert: Alert, subscription: Subscription) -> DeliveryResult:
        settings = get_settings()
        if not (settings.exotel_sid and settings.exotel_token and settings.exotel_from_number):
            return DeliveryResult("skipped", "Exotel credentials not configured")
        url = f"https://{settings.exotel_subdomain}/v1/Accounts/{settings.exotel_sid}/Sms/send.json"
        try:
            resp = httpx.post(
                url,
                data={
                    "From": settings.exotel_from_number,
                    "To": subscription.address,
                    "Body": _text_for(alert, subscription),
                },
                auth=(settings.exotel_sid, settings.exotel_token),
                timeout=10,
            )
            resp.raise_for_status()
            return DeliveryResult("sent")
        except Exception as exc:
            return DeliveryResult("failed", str(exc)[:500])


class WhatsAppAdapter:
    def send(self, alert: Alert, subscription: Subscription) -> DeliveryResult:
        settings = get_settings()
        if not (settings.whatsapp_access_token and settings.whatsapp_phone_number_id):
            return DeliveryResult("skipped", "WhatsApp credentials not configured")
        url = f"https://graph.facebook.com/{settings.whatsapp_api_version}/{settings.whatsapp_phone_number_id}/messages"
        try:
            resp = httpx.post(
                url,
                headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": subscription.address,
                    "type": "text",
                    "text": {"body": _text_for(alert, subscription)},
                },
                timeout=10,
            )
            resp.raise_for_status()
            return DeliveryResult("sent")
        except Exception as exc:
            return DeliveryResult("failed", str(exc)[:500])


_SMS_ADAPTERS = {"console": ConsoleAdapter, "twilio": TwilioAdapter, "exotel": ExotelAdapter}


def get_adapter(channel: str) -> Adapter | None:
    if channel == "telegram":
        return TelegramAdapter()
    if channel == "web_push":
        return WebPushAdapter()
    if channel == "whatsapp":
        return WhatsAppAdapter()
    if channel == "sms":
        return _SMS_ADAPTERS.get(get_settings().sms_provider, ConsoleAdapter)()
    return None
