"""Inbound WhatsApp message handling — parses Meta's webhook payload shape
and drives the shared report_conversation state machine (ingest/
report_conversation.py), the same location -> hazard -> description -> photo
flow Telegram's bot_runner.py drives. Telegram keeps its session in
python-telegram-bot's in-process context.user_data; WhatsApp's webhook calls
are stateless HTTP requests, so sessions live in Redis instead (see
report_conversation.save_session/load_session), keyed by the sender's phone
number.
"""
import logging

from sqlalchemy.orm import Session

from app.modules.ingest import report_conversation as conv
from app.modules.ingest.service import RateLimited, create_report
from app.modules.whatsapp import client

log = logging.getLogger(__name__)

CHANNEL = "whatsapp"
TRIGGER_WORDS = {"report", "hazard"}
HELP_TEXT = (
    "Reply *report* to report a coastal hazard you can see. Your report is "
    "cross-checked against ocean sensors and nearby reports before "
    "authorities see it as verified."
)


def handle_payload(db: Session, payload: dict) -> None:
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                try:
                    _handle_message(db, message)
                except Exception:
                    log.exception("Failed to handle WhatsApp message %s", message.get("id"))


def _text_body(message: dict) -> str | None:
    if message.get("type") == "text":
        return message.get("text", {}).get("body", "")
    return None


def _handle_message(db: Session, message: dict) -> None:
    from_number = message["from"]
    msg_type = message["type"]
    text = _text_body(message)

    if text is not None and text.strip().lower() == "cancel":
        conv.clear_session(CHANNEL, from_number)
        client.send_text(from_number, "Report cancelled.")
        return

    session = conv.load_session(CHANNEL, from_number)

    if session is None:
        if text is not None and text.strip().lower() in TRIGGER_WORDS:
            session, prompt = conv.start()
            conv.save_session(CHANNEL, from_number, session)
            client.send_text(from_number, prompt)
        else:
            client.send_text(from_number, HELP_TEXT)
        return

    if session.state == conv.ConvState.LOCATION:
        if msg_type != "location":
            client.send_text(from_number, "Please share your location to continue (or send *cancel*).")
            return
        loc = message["location"]
        session, prompt = conv.on_location(session, loc["latitude"], loc["longitude"])
        conv.save_session(CHANNEL, from_number, session)
        client.send_hazard_menu(from_number, prompt)
        return

    if session.state == conv.ConvState.HAZARD:
        hazard_type = _extract_hazard_choice(message, msg_type)
        if hazard_type is None:
            client.send_text(from_number, "Please choose a hazard from the list.")
            return
        session, prompt = conv.on_hazard(session, hazard_type)
        conv.save_session(CHANNEL, from_number, session)
        client.send_text(from_number, prompt)
        return

    if session.state == conv.ConvState.DESCRIPTION:
        if text is not None and text.strip().lower() == "skip":
            session, prompt = conv.skip_description(session)
        elif text is not None:
            session, prompt = conv.on_description(session, text)
        else:
            client.send_text(from_number, "Please type a description, or *skip*.")
            return
        conv.save_session(CHANNEL, from_number, session)
        client.send_text(from_number, prompt)
        return

    if session.state == conv.ConvState.PHOTO:
        if text is not None and text.strip().lower() == "skip":
            _submit(db, from_number, session, None)
        elif msg_type == "image":
            media_bytes = client.download_media(message["image"]["id"])
            _submit(db, from_number, session, media_bytes)
        else:
            client.send_text(from_number, "Please send a photo, or *skip*.")
        return


def _extract_hazard_choice(message: dict, msg_type: str) -> str | None:
    if msg_type == "interactive":
        list_reply = message.get("interactive", {}).get("list_reply")
        if list_reply and list_reply.get("id", "").startswith("hz:"):
            hazard_type = list_reply["id"].removeprefix("hz:")
            return hazard_type if hazard_type in conv.HAZARD_LABELS else None
        return None
    if msg_type == "text":
        typed = message["text"]["body"].strip().lower().replace(" ", "_")
        return typed if typed in conv.HAZARD_LABELS else None
    return None


def _submit(db: Session, from_number: str, session: conv.ReportSession, media_bytes: bytes | None) -> None:
    session = conv.mark_done(session)
    report_kwargs = conv.build_report_kwargs(session)
    try:
        report = create_report(
            db,
            source=CHANNEL,
            external_id=from_number,
            media_bytes=media_bytes,
            media_filename="whatsapp.jpg" if media_bytes else None,
            **report_kwargs,
        )
    except RateLimited as exc:
        client.send_text(from_number, f"⏳ {exc}")
        conv.clear_session(CHANNEL, from_number)
        return
    except Exception:
        log.exception("WhatsApp report submission failed")
        client.send_text(from_number, "Something went wrong; please try again.")
        conv.clear_session(CHANNEL, from_number)
        return

    conv.clear_session(CHANNEL, from_number)
    client.send_text(
        from_number,
        "Report received — thank you for keeping your coast safe.\n"
        f"Type: {conv.HAZARD_LABELS.get(report.hazard_type, report.hazard_type)}\n"
        f"Ref: {str(report.id)[:8]}\n"
        "It is now being cross-checked against ocean sensors and nearby reports.",
    )
