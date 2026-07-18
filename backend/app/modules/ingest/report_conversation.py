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

# --- Localization (phase 2, milestone 5) ------------------------------------
#
# Tamil/Telugu strings below are a first pass for the pilot deployment — not
# reviewed by a native speaker. They use standard, widely-recognized
# disaster-terminology vocabulary, but a review pass is recommended before
# relying on them beyond the pilot, especially for the hazard names
# themselves (getting "tsunami" or "storm surge" wrong is exactly the kind of
# mistake that matters here). English is always the safe fallback for an
# unrecognized or unconfigured language.
SUPPORTED_LANGS = ("en", "ta", "te")

HAZARD_LABELS_BY_LANG = {
    "en": HAZARD_LABELS,
    "ta": {
        "coastal_flooding": "🌊 கடலோர வெள்ளம்",
        "storm_surge": "🌀 புயல் அலைப்பெருக்கு",
        "high_waves": "🌊 அதிக அலைகள்",
        "tsunami": "⚠️ சுனாமி அறிகுறிகள்",
        "rip_current": "🏊 இழுவை நீரோட்டம்",
        "oil_spill": "🛢️ எண்ணெய் கசிவு",
        "algal_bloom": "🟢 பாசி பெருக்கம் / மீன் இறப்பு",
        "erosion": "🏖️ கடற்கரை அரிப்பு",
        "other": "❓ மற்றவை",
    },
    "te": {
        "coastal_flooding": "🌊 తీర వరదలు",
        "storm_surge": "🌀 తుఫాను ఉప్పెన",
        "high_waves": "🌊 ఎత్తైన అలలు",
        "tsunami": "⚠️ సునామీ సూచనలు",
        "rip_current": "🏊 లాగే ప్రవాహం",
        "oil_spill": "🛢️ చమురు లీకేజీ",
        "algal_bloom": "🟢 ఆల్గే వ్యాప్తి / చేపల మరణం",
        "erosion": "🏖️ తీర కోత",
        "other": "❓ ఇతర",
    },
}
HAZARD_SPEECH_LABELS_BY_LANG = {
    "en": HAZARD_SPEECH_LABELS,
    "ta": {
        "coastal_flooding": "கடலோர வெள்ளம்",
        "storm_surge": "புயல் அலைப்பெருக்கு",
        "high_waves": "அதிக அலைகள்",
        "tsunami": "சுனாமி அறிகுறிகள்",
        "rip_current": "இழுவை நீரோட்டம்",
        "oil_spill": "எண்ணெய் கசிவு",
        "algal_bloom": "பாசி பெருக்கம் அல்லது மீன் இறப்பு",
        "erosion": "கடற்கரை அரிப்பு",
        "other": "வேறு ஆபத்து",
    },
    "te": {
        "coastal_flooding": "తీర వరదలు",
        "storm_surge": "తుఫాను ఉప్పెన",
        "high_waves": "ఎత్తైన అలలు",
        "tsunami": "సునామీ సూచనలు",
        "rip_current": "లాగే ప్రవాహం",
        "oil_spill": "చమురు లీకేజీ",
        "algal_bloom": "ఆల్గే వ్యాప్తి లేదా చేపల మరణం",
        "erosion": "తీర కోత",
        "other": "వేరే ప్రమాదం",
    },
}
assert set(HAZARD_LABELS_BY_LANG) == set(HAZARD_SPEECH_LABELS_BY_LANG) == set(SUPPORTED_LANGS)
for _lang in SUPPORTED_LANGS:
    assert set(HAZARD_LABELS_BY_LANG[_lang]) == set(HAZARD_TYPES)
    assert set(HAZARD_SPEECH_LABELS_BY_LANG[_lang]) == set(HAZARD_TYPES)


def normalize_lang(lang: str | None) -> str:
    """Reduce a client-supplied language tag (e.g. Telegram's "ta-IN") to one
    of `SUPPORTED_LANGS`, defaulting to English for anything unrecognized."""
    code = (lang or "en").split("-")[0].lower()
    return code if code in SUPPORTED_LANGS else "en"


def hazard_menu_items(lang: str = "en") -> list[tuple[str, str]]:
    """`[(hazard_type, label), ...]` in canonical order — the source every
    channel's hazard picker (buttons, list rows, DTMF digits) builds from."""
    labels = HAZARD_LABELS_BY_LANG[normalize_lang(lang)]
    return [(hz, labels[hz]) for hz in HAZARD_TYPES]


PROMPTS_BY_LANG = {
    "en": {
        "location": "Where is the hazard? Share your location.",
        "hazard": "What do you see?",
        "description": "Describe what you see, in any language (or skip):",
        "photo": "Send a photo of the hazard (or skip):",
        "selected": "Selected",
    },
    "ta": {
        "location": "ஆபத்து எங்கே உள்ளது? உங்கள் இருப்பிடத்தை பகிரவும்.",
        "hazard": "நீங்கள் என்ன பார்க்கிறீர்கள்?",
        "description": "நீங்கள் பார்ப்பதை எந்த மொழியிலும் விவரிக்கவும் (அல்லது தவிர்க்கவும்):",
        "photo": "ஆபத்தின் புகைப்படத்தை அனுப்பவும் (அல்லது தவிர்க்கவும்):",
        "selected": "தேர்ந்தெடுக்கப்பட்டது",
    },
    "te": {
        "location": "ప్రమాదం ఎక్కడ ఉంది? మీ లొకేషన్‌ను షేర్ చేయండి.",
        "hazard": "మీరు ఏమి చూస్తున్నారు?",
        "description": "మీరు చూసేది ఏ భాషలోనైనా వివరించండి (లేదా దాటవేయండి):",
        "photo": "ప్రమాదం యొక్క ఫోటోను పంపండి (లేదా దాటవేయండి):",
        "selected": "ఎంచుకున్నారు",
    },
}
assert set(PROMPTS_BY_LANG) == set(SUPPORTED_LANGS)

# Backward-compatible English-only names some existing call sites still use.
PROMPT_LOCATION = PROMPTS_BY_LANG["en"]["location"]
PROMPT_HAZARD = PROMPTS_BY_LANG["en"]["hazard"]
PROMPT_DESCRIPTION = PROMPTS_BY_LANG["en"]["description"]
PROMPT_PHOTO = PROMPTS_BY_LANG["en"]["photo"]


@dataclass
class ReportSession:
    state: ConvState = ConvState.LOCATION
    lang: str = "en"
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


def start(lang: str = "en") -> tuple[ReportSession, str]:
    lang = normalize_lang(lang)
    return ReportSession(lang=lang), PROMPTS_BY_LANG[lang]["location"]


def on_location(session: ReportSession, lat: float, lon: float) -> tuple[ReportSession, str]:
    if session.state != ConvState.LOCATION:
        raise ConversationError(f"expected LOCATION, session is in {session.state}")
    session.lat, session.lon = lat, lon
    session.state = ConvState.HAZARD
    return session, PROMPTS_BY_LANG[session.lang]["hazard"]


def on_hazard(session: ReportSession, hazard_type: str) -> tuple[ReportSession, str]:
    if session.state != ConvState.HAZARD:
        raise ConversationError(f"expected HAZARD, session is in {session.state}")
    if hazard_type not in HAZARD_LABELS:
        raise ConversationError(f"unknown hazard_type {hazard_type!r}")
    session.hazard_type = hazard_type
    session.state = ConvState.DESCRIPTION
    prompts = PROMPTS_BY_LANG[session.lang]
    label = HAZARD_LABELS_BY_LANG[session.lang][hazard_type]
    return session, f"{prompts['selected']}: {label}\n\n{prompts['description']}"


def on_description(session: ReportSession, text: str | None) -> tuple[ReportSession, str]:
    if session.state != ConvState.DESCRIPTION:
        raise ConversationError(f"expected DESCRIPTION, session is in {session.state}")
    session.text = text
    session.state = ConvState.PHOTO
    return session, PROMPTS_BY_LANG[session.lang]["photo"]


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
