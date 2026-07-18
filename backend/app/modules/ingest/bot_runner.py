"""Telegram bot (long-polling) — run as its own process/container:
    python -m app.modules.ingest.bot_runner

Reports submitted here go through the exact same create_report pipeline as the
web API, so classification, dedup, and scoring treat all channels identically.
The report flow itself (location -> hazard -> description -> photo) is owned
by report_conversation.py, shared with the WhatsApp adapter — this module is
a thin translator between Telegram's Update/context objects and that shared
state machine, plus Telegram-only features (subscribe, voice notes, /ask).
"""
import asyncio
import logging
import sys

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import Subscription
from app.modules.alerts.geofence import cells_around
from app.modules.chat import service as chat_service
from app.modules.ingest import report_conversation as conv
from app.modules.ingest import voice
from app.modules.ingest.service import RateLimited, create_report

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

SUBSCRIBE_LOCATION = 100  # separate state space; independent ConversationHandler


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🌊 *OceanPing* — coastal hazard reporting.\n\n"
        "Use /report to report something you can see happening on the coast.\n"
        "Your report is cross-checked against ocean sensors and nearby reports "
        "before authorities see it as verified.\n\n"
        "Use /subscribe to get alerts for hazards near a place you care about.\n\n"
        "Use /ask followed by a question to ask about hazards, alert tiers, or "
        "coastal safety in general.\n\n"
        "Commands:\n/report — submit a hazard report\n"
        "/subscribe — get alerts for an area\n/unsubscribe — stop alerts\n"
        "/ask <question> — ask the hazard info assistant\n"
        "/cancel — abort a report",
        parse_mode="Markdown",
    )


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Share a location", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "Share a location to subscribe to hazard alerts near it (your home, "
        "your boat's harbour, a relative's village…).",
        reply_markup=keyboard,
    )
    return SUBSCRIBE_LOCATION


async def on_subscribe_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    loc = update.message.location
    chat_id = str(update.effective_chat.id)
    lang = (update.effective_user.language_code or "en").split("-")[0]
    settings = get_settings()

    def _upsert():
        db = SessionLocal()
        try:
            cells = cells_around(loc.latitude, loc.longitude, settings.subscription_radius_rings)
            sub = db.scalar(
                select(Subscription)
                .where(Subscription.channel == "telegram")
                .where(Subscription.address == chat_id)
            )
            if sub is None:
                sub = Subscription(channel="telegram", address=chat_id)
                db.add(sub)
            sub.h3_cells = cells
            sub.lang = lang if lang in ("en", "hi", "ta") else "en"
            db.commit()
        finally:
            db.close()

    await asyncio.to_thread(_upsert)
    await update.message.reply_text(
        "✅ Subscribed. You'll get an alert here when a hazard is reported and "
        "corroborated near that location. Use /unsubscribe to stop.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)

    def _delete() -> int:
        db = SessionLocal()
        try:
            n = db.query(Subscription).filter_by(channel="telegram", address=chat_id).delete()
            db.commit()
            return n
        finally:
            db.close()

    n = await asyncio.to_thread(_delete)
    await update.message.reply_text("Unsubscribed." if n else "You weren't subscribed to anything.")


def _hazard_keyboard() -> InlineKeyboardMarkup:
    rows, row = [], []
    for hazard_type, label in conv.hazard_menu_items():
        row.append(InlineKeyboardButton(label, callback_data=f"hz:{hazard_type}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> conv.ConvState:
    context.user_data.clear()
    session, prompt = conv.start()
    context.user_data["session"] = session
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Share my location", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        f"{prompt.rstrip('.')} (or send any location via 📎 → Location).",
        reply_markup=keyboard,
    )
    return session.state


async def on_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> conv.ConvState:
    loc = update.message.location
    session, prompt = conv.on_location(context.user_data["session"], loc.latitude, loc.longitude)
    context.user_data["session"] = session
    await update.message.reply_text(prompt, reply_markup=_hazard_keyboard())
    await update.message.reply_text("…", reply_markup=ReplyKeyboardRemove())
    return session.state


async def on_hazard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> conv.ConvState:
    query = update.callback_query
    await query.answer()
    hazard_type = query.data.removeprefix("hz:")
    session, prompt = conv.on_hazard(context.user_data["session"], hazard_type)
    context.user_data["session"] = session
    await query.edit_message_text(prompt.replace("(or skip):", "(or /skip):"))
    return session.state


async def on_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> conv.ConvState:
    session, prompt = conv.on_description(context.user_data["session"], update.message.text)
    context.user_data["session"] = session
    await update.message.reply_text(("Got it. " + prompt).replace("(or skip):", "(or /skip):"))
    return session.state


async def skip_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> conv.ConvState:
    session, prompt = conv.skip_description(context.user_data["session"])
    context.user_data["session"] = session
    await update.message.reply_text(prompt.replace("(or skip):", "(or /skip):"))
    return session.state


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> conv.ConvState:
    file = await update.message.voice.get_file()
    audio_bytes = bytes(await file.download_as_bytearray())
    await update.message.reply_text("🎙️ Transcribing your voice note…")
    transcript = await asyncio.to_thread(voice.transcribe, audio_bytes)
    if transcript:
        await update.message.reply_text(f"Heard: “{transcript}”")
    else:
        await update.message.reply_text("Couldn't transcribe that — continuing without a description.")
    session, prompt = conv.on_description(context.user_data["session"], transcript)
    context.user_data["session"] = session
    await update.message.reply_text(prompt.replace("(or skip):", "(or /skip):"))
    return session.state


async def _submit(update: Update, context: ContextTypes.DEFAULT_TYPE,
                  media_bytes: bytes | None) -> int:
    user = update.effective_user
    session = conv.mark_done(context.user_data["session"])
    report_kwargs = conv.build_report_kwargs(session)

    def _create():
        db = SessionLocal()
        try:
            return create_report(
                db,
                source="telegram",
                external_id=str(user.id),
                media_bytes=media_bytes,
                media_filename="telegram.jpg" if media_bytes else None,
                **report_kwargs,
            )
        finally:
            db.close()

    try:
        report = await asyncio.to_thread(_create)
    except RateLimited as exc:
        await update.message.reply_text(f"⏳ {exc}")
        return ConversationHandler.END
    except Exception:
        log.exception("Report submission failed")
        await update.message.reply_text("⚠️ Something went wrong; please try again.")
        return ConversationHandler.END

    await update.message.reply_text(
        "✅ Report received — thank you for keeping your coast safe.\n"
        f"Type: {conv.HAZARD_LABELS.get(report.hazard_type, report.hazard_type)}\n"
        f"Ref: {str(report.id)[:8]}\n"
        "It is now being cross-checked against ocean sensors and nearby reports."
    )
    return ConversationHandler.END


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    file = await update.message.photo[-1].get_file()
    data = bytes(await file.download_as_bytearray())
    return await _submit(update, context, data)


async def skip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _submit(update, context, None)


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    question = " ".join(context.args) if context.args else ""
    if not question.strip():
        await update.message.reply_text(
            "Ask a question after /ask, e.g. `/ask what does watch tier mean?`",
            parse_mode="Markdown",
        )
        return
    chat_id = str(update.effective_chat.id)

    def _answer():
        db = SessionLocal()
        try:
            sub = db.scalar(
                select(Subscription)
                .where(Subscription.channel == "telegram")
                .where(Subscription.address == chat_id)
            )
            cells = sub.h3_cells if sub and sub.h3_cells else None
            return chat_service.answer(db, question, channel="telegram", alert_cells=cells)
        finally:
            db.close()

    result = await asyncio.to_thread(_answer)
    text = result["answer"]
    if result.get("alerts"):
        lines = [f"⚠️ {a['tier'].upper()}: {a['message']}" for a in result["alerts"]]
        text += "\n\nActive alerts near your subscribed location:\n" + "\n".join(lines)
    await update.message.reply_text(text)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Report cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def main() -> None:
    token = get_settings().telegram_bot_token
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN not set — bot disabled. Exiting cleanly.")
        sys.exit(0)

    app = ApplicationBuilder().token(token).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("report", cmd_report)],
        states={
            conv.ConvState.LOCATION: [MessageHandler(filters.LOCATION, on_location)],
            conv.ConvState.HAZARD: [CallbackQueryHandler(on_hazard, pattern=r"^hz:")],
            conv.ConvState.DESCRIPTION: [
                CommandHandler("skip", skip_description),
                MessageHandler(filters.VOICE, on_voice),
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_description),
            ],
            conv.ConvState.PHOTO: [
                CommandHandler("skip", skip_photo),
                MessageHandler(filters.PHOTO, on_photo),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    subscribe_conv = ConversationHandler(
        entry_points=[CommandHandler("subscribe", cmd_subscribe)],
        states={SUBSCRIBE_LOCATION: [MessageHandler(filters.LOCATION, on_subscribe_location)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(conv_handler)
    app.add_handler(subscribe_conv)
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("ask", cmd_ask))
    log.info("OceanPing Telegram bot polling…")
    app.run_polling()


if __name__ == "__main__":
    main()
