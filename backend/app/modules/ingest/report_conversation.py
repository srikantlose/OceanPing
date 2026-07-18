"""Channel-agnostic hazard-report conversation core (phase 2, milestone 4).

Telegram, WhatsApp, and (partially — see modules/ivr) IVR all walk a citizen
through the same location -> hazard -> description -> photo flow before
handing off to ingest/service.py::create_report(). This module holds that
flow once — the hazard menu, the prompts, and the state transitions — so
each channel adapter (bot_runner.py, modules/whatsapp/service.py) is a thin
translator between its own message format and this shared state machine,
not a second hand-maintained copy of it.

Telegram keeps its session in python-telegram-bot's in-process
`context.user_data` (a `ReportSession` lives there for the life of the
conversation). WhatsApp's webhook calls are stateless HTTP requests with no
equivalent in-process home for that object between messages, so this module
also offers a small Redis-backed store (`save_session`/`load_session`/
`clear_session`) for channels that need it — same Redis client and key-style
convention as `ingest/service.py`'s rate limiter.
"""
import json
import logging
from dataclasses import asdict, dataclass
from enum import Enum

from app.core.redisclient import get_redis
from app.models import HAZARD_TYPES

log = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 900  # 15 min — an abandoned conversation expires rather than leaking forever


class ConvState(str, Enum):
    LOCATION = "location"
    HAZARD = "hazard"
    DESCRIPTION = "description"
    PHOTO = "photo"
    DONE = "done"


# Canonical hazard menu, in `HAZARD_TYPES` order so every channel numbers/lists
# hazards identically. "other" is deliberately last, matching HAZARD_TYPES.
HAZARD_LABELS = {
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

# Plain-text equivalents for channels that can't render emoji sensibly
# (IVR text-to-speech reads them aloud oddly or drops them).
HAZARD_SPEECH_LABELS = {
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

assert set(HAZARD_LABELS) == set(HAZARD_TYPES) == set(HAZARD_SPEECH_LABELS)


def hazard_menu_items() -> list[tuple[str, str]]:
    """`[(hazard_type, label), ...]` in canonical order — the source every
    channel's hazard picker (buttons, list rows, DTMF digits) builds from."""
    return [(hz, HAZARD_LABELS[hz]) for hz in HAZARD_TYPES]


PROMPT_LOCATION = "Where is the hazard? Share your location."
PROMPT_HAZARD = "What do you see?"
PROMPT_DESCRIPTION = "Describe what you see, in any language (or skip):"
PROMPT_PHOTO = "Send a photo of the hazard (or skip):"


@dataclass
class ReportSession:
    state: ConvState = ConvState.LOCATION
    lat: float | None = None
    lon: float | None = None
    hazard_type: str | None = None
    text: str | None = None

    def to_json(self) -> str:
        data = asdict(self)
        data["state"] = self.state.value
        return json.dumps(data)

    @classmethod
    def from_json(cls, raw: str) -> "ReportSession":
        data = json.loads(raw)
        data["state"] = ConvState(data["state"])
        return cls(**data)


class ConversationError(Exception):
    """Raised when a channel adapter sends an input that doesn't match the
    session's current state (e.g. a hazard pick while awaiting location) —
    adapters should catch this and re-prompt rather than let it propagate."""


def start() -> tuple[ReportSession, str]:
    return ReportSession(), PROMPT_LOCATION


def on_location(session: ReportSession, lat: float, lon: float) -> tuple[ReportSession, str]:
    if session.state != ConvState.LOCATION:
        raise ConversationError(f"expected LOCATION, session is in {session.state}")
    session.lat, session.lon = lat, lon
    session.state = ConvState.HAZARD
    return session, PROMPT_HAZARD


def on_hazard(session: ReportSession, hazard_type: str) -> tuple[ReportSession, str]:
    if session.state != ConvState.HAZARD:
        raise ConversationError(f"expected HAZARD, session is in {session.state}")
    if hazard_type not in HAZARD_LABELS:
        raise ConversationError(f"unknown hazard_type {hazard_type!r}")
    session.hazard_type = hazard_type
    session.state = ConvState.DESCRIPTION
    return session, f"Selected: {HAZARD_LABELS[hazard_type]}\n\n{PROMPT_DESCRIPTION}"


def on_description(session: ReportSession, text: str | None) -> tuple[ReportSession, str]:
    if session.state != ConvState.DESCRIPTION:
        raise ConversationError(f"expected DESCRIPTION, session is in {session.state}")
    session.text = text
    session.state = ConvState.PHOTO
    return session, PROMPT_PHOTO


def skip_description(session: ReportSession) -> tuple[ReportSession, str]:
    return on_description(session, None)


def ready_for_photo(session: ReportSession) -> bool:
    return session.state == ConvState.PHOTO


def mark_done(session: ReportSession) -> ReportSession:
    if session.state != ConvState.PHOTO:
        raise ConversationError(f"expected PHOTO, session is in {session.state}")
    session.state = ConvState.DONE
    return session


def build_report_kwargs(session: ReportSession) -> dict:
    """Assembles the subset of `create_report()`'s kwargs this conversation
    core is responsible for — the caller still supplies db/source/external_id
    and any media bytes, which are channel-specific."""
    return {"lat": session.lat, "lon": session.lon, "hazard_type": session.hazard_type, "text": session.text}


# --- Redis-backed session store (for stateless-webhook channels) -----------

def _key(channel: str, external_id: str) -> str:
    return f"report_conv:{channel}:{external_id}"


def load_session(channel: str, external_id: str) -> ReportSession | None:
    try:
        raw = get_redis().get(_key(channel, external_id))
    except Exception:
        log.exception("Redis unavailable; cannot load conversation session")
        return None
    return ReportSession.from_json(raw) if raw else None


def save_session(channel: str, external_id: str, session: ReportSession) -> None:
    try:
        get_redis().set(_key(channel, external_id), session.to_json(), ex=SESSION_TTL_SECONDS)
    except Exception:
        log.exception("Redis unavailable; conversation session not persisted")


def clear_session(channel: str, external_id: str) -> None:
    try:
        get_redis().delete(_key(channel, external_id))
    except Exception:
        log.exception("Redis unavailable; conversation session not cleared")
