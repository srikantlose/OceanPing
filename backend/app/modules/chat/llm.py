"""LLM adapter for the RAG chatbot: a plain httpx call to the Anthropic
Messages API, gated on settings.anthropic_api_key exactly like
delivery/adapters.py's TwilioAdapter/ExotelAdapter are gated on their own
credentials - unconfigured means "skip", never a crash, and the caller
(chat/service.py) treats a None return the same as a low-retrieval-score
miss: fall back to the helpline message.
"""
import logging

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 512


class AnthropicAdapter:
    def complete(self, system: str, user_message: str) -> str | None:
        settings = get_settings()
        if not settings.anthropic_api_key:
            log.info("AnthropicAdapter skipped: ANTHROPIC_API_KEY not configured")
            return None
        try:
            resp = httpx.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": settings.anthropic_model,
                    "max_tokens": MAX_TOKENS,
                    "system": system,
                    "messages": [{"role": "user", "content": user_message}],
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            text = "".join(
                part.get("text", "") for part in data.get("content", []) if part.get("type") == "text"
            ).strip()
            return text or None
        except Exception:
            log.exception("Anthropic completion failed")
            return None


def get_adapter() -> AnthropicAdapter:
    return AnthropicAdapter()
