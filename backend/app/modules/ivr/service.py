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
plan's wording) is deferred to milestone 5, which already scopes Tamil/
Telugu prompt localization — a language menu is meaningless without
translated prompt strings to switch to, so it isn't half-built here.
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


def _hazard_menu_say() -> str:
    lines = [f"Press {i} for {conv.HAZARD_SPEECH_LABELS[hz]}." for i, (hz, _label) in enumerate(conv.hazard_menu_items(), start=1)]
    return "Welcome to OceanPing. " + " ".join(lines)


def _location_menu_say() -> str:
    lines = [f"Press {loc['digit']} for {loc['name']}." for loc in ivr_locations.PILOT_LOCATIONS]
    return "Which area are you near? " + " ".join(lines)


def _gather(say_text: str, action_step: str) -> str:
    action = f"/webhooks/ivr/voice?step={action_step}"
    return f'<Gather numDigits="1" timeout="10" action="{escape(action)}" method="POST">{_say(say_text)}</Gather>' + _say(
        "We did not receive your input. Goodbye."
    )


def handle_start() -> str:
    return _twiml(_gather(_hazard_menu_say(), "hazard"))


def handle_hazard(call_sid: str, digit: str) -> str:
    items = conv.hazard_menu_items()
    try:
        idx = int(digit)
        if not (1 <= idx <= len(items)):
            raise ValueError(f"digit out of range: {digit!r}")
        hazard_type = items[idx - 1][0]
    except ValueError:
        return _twiml(_say("Invalid selection. Goodbye."))
    _save(call_sid, {"hazard_type": hazard_type})
    return _twiml(_gather(_location_menu_say(), "location"))


def handle_location(call_sid: str, digit: str) -> str:
    location = ivr_locations.location_for_digit(digit)
    if location is None:
        return _twiml(_say("Invalid selection. Goodbye."))
    session = _load(call_sid)
    session["lat"], session["lon"] = location["lat"], location["lon"]
    _save(call_sid, session)
    settings = get_settings()
    record = (
        f'<Record action="{escape("/webhooks/ivr/voice?step=recording")}" method="POST" '
        f'maxLength="{settings.ivr_recording_max_seconds}" finishOnKey="#" playBeep="true"/>'
    )
    return _twiml(_say("After the beep, describe what you see. Press pound when done."), record)


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
    hazard_type = session.get("hazard_type")
    lat, lon = session.get("lat"), session.get("lon")
    if hazard_type is None or lat is None or lon is None:
        _clear(call_sid)
        return _twiml(_say("Something went wrong with your call session. Please call again. Goodbye."))

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
        return _twiml(_say("You have submitted too many reports recently. Please try again later. Goodbye."))
    except Exception:
        log.exception("IVR report submission failed")
        _clear(call_sid)
        return _twiml(_say("Something went wrong submitting your report. Goodbye."))

    _clear(call_sid)
    return _twiml(_say("Thank you. Your report has been received and is being checked. Goodbye."))
