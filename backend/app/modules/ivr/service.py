"""Exotel/Twilio Voice IVR flow: hazard digit -> location digit -> recorded
description -> create_report(source="ivr"). TwiML in, TwiML out — Exotel's
classic Exoml call-control markup is Twilio-compatible for the Gather/Say/
Record verbs used here, so this one flow serves either provider.

Unlike the Telegram/WhatsApp flow (free-text location share, photo, typed or
voice-note description), a phone call has no GPS and no camera: "location" is
a caller-selected item from a short list of named pilot locations
(modules/ivr/locations.py) standing in for a real registered-village/
cell-tower lookup, and "photo" doesn't exist at all — the description is a
recorded voice note run through the same Whisper transcription
(ingest/voice.py) the Telegram bot already uses for voice messages.

Call state (which hazard/location digit was already picked) lives in Redis
keyed by Twilio's CallSid, since each webhook call is a fresh, stateless HTTP
request — same reasoning as the WhatsApp session store, but a plain dict
here rather than report_conversation.ReportSession, since the IVR flow's
shape (digit menus, no free-text location/photo) doesn't match that state
machine.

Language selection ("language -> hazard digit -> location" in the phase-2
plan's wording) is now the first step (phase 2, milestone 5), using the same
Tamil/Telugu strings report_conversation.py's hazard labels/prompts added —
see IVR_STRINGS for the IVR-specific framing text around them (menu intros,
error/closing messages). The language-select menu itself is necessarily
announced in all three languages at once, since the caller hasn't picked one
yet — the same thing real multi-language IVR systems do.
"""
import json
import logging
from xml.sax.saxutils import escape

import httpx

from app.core.config import get_settings
from app.core.redisclient import get_redis
from app.modules.ingest import report_conversation as conv
from app.modules.ingest import voice
from app.modules.ingest.service import RateLimited, create_report
from app.modules.ivr import locations as ivr_locations

log = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 900

# Digit -> language code for the opening language-select menu.
LANGUAGE_DIGITS = {"1": "en", "2": "ta", "3": "te"}

LANGUAGE_MENU_SAY = (
    "Welcome to OceanPing. For English, press 1. "
    "தமிழுக்கு, 2 அழுத்தவும். "
    "తెలుగు కోసం, 3 నొక్కండి."
)

# IVR-specific framing text (menu intros, error/closing messages) around the
# hazard labels and press-digit menus. Same translation-quality caveat as
# report_conversation.py's strings: a first pass for the pilot, not reviewed
# by a native speaker.
IVR_STRINGS = {
    "en": {
        "welcome": "Welcome to OceanPing.",
        "which_area": "Which area are you near?",
        "after_beep": "After the beep, describe what you see. Press pound when done.",
        "invalid_selection": "Invalid selection. Goodbye.",
        "no_input": "We did not receive your input. Goodbye.",
        "session_lost": "Something went wrong with your call session. Please call again. Goodbye.",
        "rate_limited": "You have submitted too many reports recently. Please try again later. Goodbye.",
        "submission_failed": "Something went wrong submitting your report. Goodbye.",
        "thank_you": "Thank you. Your report has been received and is being checked. Goodbye.",
        "press_template": "Press {digit} for {label}.",
    },
    "ta": {
        "welcome": "ஓசன்பிங்கிற்கு வரவேற்கிறோம்.",
        "which_area": "நீங்கள் எந்த பகுதிக்கு அருகில் இருக்கிறீர்கள்?",
        "after_beep": "பீப் ஒலிக்குப் பிறகு, நீங்கள் பார்ப்பதை விவரிக்கவும். முடிந்ததும் ஹேஷ் பொத்தானை அழுத்தவும்.",
        "invalid_selection": "தவறான தேர்வு. குட்பை.",
        "no_input": "உங்கள் பதிலைப் பெறவில்லை. குட்பை.",
        "session_lost": "உங்கள் அழைப்பு அமர்வில் ஒரு பிழை ஏற்பட்டது. மீண்டும் அழைக்கவும். குட்பை.",
        "rate_limited": "நீங்கள் சமீபத்தில் அதிக அறிக்கைகளை சமர்ப்பித்துள்ளீர்கள். பின்னர் முயற்சிக்கவும். குட்பை.",
        "submission_failed": "உங்கள் அறிக்கையை சமர்ப்பிப்பதில் சிக்கல் ஏற்பட்டது. குட்பை.",
        "thank_you": "நன்றி. உங்கள் அறிக்கை பெறப்பட்டது மற்றும் சரிபார்க்கப்படுகிறது. குட்பை.",
        "press_template": "{label}-க்கு {digit} அழுத்தவும்.",
    },
    "te": {
        "welcome": "ఓషన్‌పింగ్‌కు స్వాగతం.",
        "which_area": "మీరు ఏ ప్రాంతానికి దగ్గరగా ఉన్నారు?",
        "after_beep": "బీప్ తర్వాత, మీరు చూసేది వివరించండి. పూర్తయిన తర్వాత హాష్ నొక్కండి.",
        "invalid_selection": "తప్పు ఎంపిక. వీడ్కోలు.",
        "no_input": "మీ సమాధానం అందలేదు. వీడ్కోలు.",
        "session_lost": "మీ కాల్ సెషన్‌లో సమస్య ఏర్పడింది. దయచేసి మళ్లీ కాల్ చేయండి. వీడ్కోలు.",
        "rate_limited": "మీరు ఇటీవల చాలా నివేదికలు సమర్పించారు. దయచేసి తర్వాత ప్రయత్నించండి. వీడ్కోలు.",
        "submission_failed": "మీ నివేదికను సమర్పించడంలో సమస్య ఏర్పడింది. వీడ్కోలు.",
        "thank_you": "ధన్యవాదాలు. మీ నివేదిక అందుకోబడింది మరియు తనిఖీ చేయబడుతోంది. వీడ్కోలు.",
        "press_template": "{label} కోసం {digit} నొక్కండి.",
    },
}


def _key(call_sid: str) -> str:
    return f"ivr_session:{call_sid}"


def _load(call_sid: str) -> dict:
    try:
        raw = get_redis().get(_key(call_sid))
    except Exception:
        log.exception("Redis unavailable; IVR session lost")
        return {}
    return json.loads(raw) if raw else {}


def _save(call_sid: str, session: dict) -> None:
    try:
        get_redis().set(_key(call_sid), json.dumps(session), ex=SESSION_TTL_SECONDS)
    except Exception:
        log.exception("Redis unavailable; IVR session not persisted")


def _clear(call_sid: str) -> None:
    try:
        get_redis().delete(_key(call_sid))
    except Exception:
        log.exception("Redis unavailable; IVR session not cleared")


def _say(text: str) -> str:
    return f"<Say>{escape(text)}</Say>"


def _twiml(*body: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(body) + "</Response>"


def _hazard_menu_say(lang: str) -> str:
    strings = IVR_STRINGS[lang]
    speech_labels = conv.HAZARD_SPEECH_LABELS_BY_LANG[lang]
    lines = [
        strings["press_template"].format(digit=i, label=speech_labels[hz])
        for i, (hz, _label) in enumerate(conv.hazard_menu_items(lang), start=1)
    ]
    return strings["welcome"] + " " + " ".join(lines)


def _location_menu_say(lang: str) -> str:
    strings = IVR_STRINGS[lang]
    lines = [
        strings["press_template"].format(digit=loc["digit"], label=loc["name"])
        for loc in ivr_locations.PILOT_LOCATIONS
    ]
    return strings["which_area"] + " " + " ".join(lines)


def _gather(say_text: str, action_step: str, lang: str = "en") -> str:
    action = f"/webhooks/ivr/voice?step={action_step}"
    return f'<Gather numDigits="1" timeout="10" action="{escape(action)}" method="POST">{_say(say_text)}</Gather>' + _say(
        IVR_STRINGS[lang]["no_input"]
    )


def handle_start() -> str:
    return _twiml(_gather(LANGUAGE_MENU_SAY, "language"))


def handle_language(call_sid: str, digit: str) -> str:
    lang = LANGUAGE_DIGITS.get(digit)
    if lang is None:
        return _twiml(_say(IVR_STRINGS["en"]["invalid_selection"]))
    _save(call_sid, {"lang": lang})
    return _twiml(_gather(_hazard_menu_say(lang), "hazard", lang))


def handle_hazard(call_sid: str, digit: str) -> str:
    session = _load(call_sid)
    lang = session.get("lang", "en")
    items = conv.hazard_menu_items(lang)
    try:
        idx = int(digit)
        if not (1 <= idx <= len(items)):
            raise ValueError(f"digit out of range: {digit!r}")
        hazard_type = items[idx - 1][0]
    except ValueError:
        return _twiml(_say(IVR_STRINGS[lang]["invalid_selection"]))
    session["hazard_type"] = hazard_type
    _save(call_sid, session)
    return _twiml(_gather(_location_menu_say(lang), "location", lang))


def handle_location(call_sid: str, digit: str) -> str:
    session = _load(call_sid)
    lang = session.get("lang", "en")
    location = ivr_locations.location_for_digit(digit)
    if location is None:
        return _twiml(_say(IVR_STRINGS[lang]["invalid_selection"]))
    session["lat"], session["lon"] = location["lat"], location["lon"]
    _save(call_sid, session)
    settings = get_settings()
    record = (
        f'<Record action="{escape("/webhooks/ivr/voice?step=recording")}" method="POST" '
        f'maxLength="{settings.ivr_recording_max_seconds}" finishOnKey="#" playBeep="true"/>'
    )
    return _twiml(_say(IVR_STRINGS[lang]["after_beep"]), record)


def _download_recording(recording_url: str) -> bytes | None:
    settings = get_settings()
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        log.info("Twilio credentials not configured; skipping IVR recording download")
        return None
    try:
        resp = httpx.get(
            f"{recording_url}.mp3",
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.content
    except Exception:
        log.exception("Failed to download IVR recording")
        return None


def handle_recording(db, call_sid: str, from_number: str, recording_url: str | None) -> str:
    session = _load(call_sid)
    lang = session.get("lang", "en")
    hazard_type = session.get("hazard_type")
    lat, lon = session.get("lat"), session.get("lon")
    if hazard_type is None or lat is None or lon is None:
        _clear(call_sid)
        return _twiml(_say(IVR_STRINGS[lang]["session_lost"]))

    transcript = None
    if recording_url:
        audio_bytes = _download_recording(recording_url)
        if audio_bytes:
            transcript = voice.transcribe(audio_bytes)

    try:
        create_report(
            db,
            source="ivr",
            external_id=from_number or call_sid,
            lat=lat,
            lon=lon,
            hazard_type=hazard_type,
            text=transcript,
        )
    except RateLimited:
        _clear(call_sid)
        return _twiml(_say(IVR_STRINGS[lang]["rate_limited"]))
    except Exception:
        log.exception("IVR report submission failed")
        _clear(call_sid)
        return _twiml(_say(IVR_STRINGS[lang]["submission_failed"]))

    _clear(call_sid)
    return _twiml(_say(IVR_STRINGS[lang]["thank_you"]))
