import asyncio
from types import SimpleNamespace

from app.modules.ingest import bot_runner, report_conversation as conv
from app.modules.ingest.service import RateLimited


class FakeMessage:
    def __init__(self, text=None, location=None, photo=None, contact=None):
        self.text = text
        self.location = location
        self.photo = photo
        self.contact = contact
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append(text)


class FakeCallbackQuery:
    def __init__(self, data):
        self.data = data
        self.edited = []

    async def answer(self):
        pass

    async def edit_message_text(self, text, **kwargs):
        self.edited.append(text)


class FakePhotoFile:
    def __init__(self, data=b"fake-jpeg-bytes"):
        self._data = data

    async def download_as_bytearray(self):
        return bytearray(self._data)


class FakePhotoSize:
    async def get_file(self):
        return FakePhotoFile()


class FakeContact:
    def __init__(self, phone_number):
        self.phone_number = phone_number


class FakeUpdate:
    def __init__(self, message=None, callback_query=None, user_id=42, chat_id=42, language_code=None):
        self.message = message
        self.callback_query = callback_query
        self.effective_user = SimpleNamespace(id=user_id, language_code=language_code)
        self.effective_chat = SimpleNamespace(id=chat_id)


class FakeContext:
    def __init__(self):
        self.user_data = {}
        self.args = []


def _no_op_session_local():
    return SimpleNamespace(close=lambda: None)


def test_hazard_keyboard_has_one_button_per_hazard_type():
    keyboard = bot_runner._hazard_keyboard()
    buttons = [btn for row in keyboard.inline_keyboard for btn in row]
    assert len(buttons) == 9
    assert {btn.callback_data for btn in buttons} == {f"hz:{hz}" for hz in conv.HAZARD_LABELS}


def test_full_report_flow_creates_report(monkeypatch):
    captured = {}

    class FakeReport:
        id = "33333333-3333-3333-3333-333333333333"
        hazard_type = "oil_spill"

    monkeypatch.setattr(bot_runner, "create_report", lambda db, **kw: captured.update(kw) or FakeReport())
    monkeypatch.setattr(bot_runner, "SessionLocal", _no_op_session_local)

    context = FakeContext()

    async def run():
        state = await bot_runner.cmd_report(FakeUpdate(message=FakeMessage()), context)
        assert state == conv.ConvState.LOCATION

        loc = SimpleNamespace(latitude=13.05, longitude=80.28)
        state = await bot_runner.on_location(FakeUpdate(message=FakeMessage(location=loc)), context)
        assert state == conv.ConvState.HAZARD

        cq = FakeCallbackQuery("hz:oil_spill")
        state = await bot_runner.on_hazard(FakeUpdate(callback_query=cq), context)
        assert state == conv.ConvState.DESCRIPTION
        assert "Oil spill" in cq.edited[0] or "oil spill" in cq.edited[0].lower()

        state = await bot_runner.on_description(
            FakeUpdate(message=FakeMessage(text="black slick near the shore")), context
        )
        assert state == conv.ConvState.PHOTO

        msg = FakeMessage(photo=[FakePhotoSize()])
        result = await bot_runner.on_photo(FakeUpdate(message=msg), context)
        assert result == bot_runner.ConversationHandler.END
        assert "Ref:" in msg.sent[-1]

    asyncio.run(run())

    assert captured["source"] == "telegram"
    assert captured["external_id"] == "42"
    assert captured["lat"] == 13.05 and captured["lon"] == 80.28
    assert captured["hazard_type"] == "oil_spill"
    assert captured["text"] == "black slick near the shore"
    assert captured["media_bytes"] == b"fake-jpeg-bytes"
    assert captured["media_filename"] == "telegram.jpg"


def test_skip_description_and_skip_photo_submits_without_text_or_media(monkeypatch):
    captured = {}

    class FakeReport:
        id = "44444444-4444-4444-4444-444444444444"
        hazard_type = "erosion"

    monkeypatch.setattr(bot_runner, "create_report", lambda db, **kw: captured.update(kw) or FakeReport())
    monkeypatch.setattr(bot_runner, "SessionLocal", _no_op_session_local)

    context = FakeContext()

    async def run():
        await bot_runner.cmd_report(FakeUpdate(message=FakeMessage()), context)
        loc = SimpleNamespace(latitude=1.0, longitude=2.0)
        await bot_runner.on_location(FakeUpdate(message=FakeMessage(location=loc)), context)
        await bot_runner.on_hazard(FakeUpdate(callback_query=FakeCallbackQuery("hz:erosion")), context)
        await bot_runner.skip_description(FakeUpdate(message=FakeMessage()), context)
        return await bot_runner.skip_photo(FakeUpdate(message=FakeMessage()), context)

    result = asyncio.run(run())
    assert result == bot_runner.ConversationHandler.END
    assert captured["text"] is None
    assert captured["media_bytes"] is None


def test_rate_limited_submission_replies_without_raising(monkeypatch):
    def raise_rate_limited(db, **kw):
        raise RateLimited("too many reports")

    monkeypatch.setattr(bot_runner, "create_report", raise_rate_limited)
    monkeypatch.setattr(bot_runner, "SessionLocal", _no_op_session_local)

    context = FakeContext()

    async def run():
        await bot_runner.cmd_report(FakeUpdate(message=FakeMessage()), context)
        loc = SimpleNamespace(latitude=1.0, longitude=2.0)
        await bot_runner.on_location(FakeUpdate(message=FakeMessage(location=loc)), context)
        await bot_runner.on_hazard(FakeUpdate(callback_query=FakeCallbackQuery("hz:erosion")), context)
        await bot_runner.skip_description(FakeUpdate(message=FakeMessage()), context)
        msg = FakeMessage()
        result = await bot_runner.skip_photo(FakeUpdate(message=msg), context)
        return result, msg

    result, msg = asyncio.run(run())
    assert result == bot_runner.ConversationHandler.END
    assert any("too many reports" in text for text in msg.sent)


def test_cancel_clears_user_data():
    context = FakeContext()
    context.user_data["session"] = "leftover"
    msg = FakeMessage()

    asyncio.run(bot_runner.cmd_cancel(FakeUpdate(message=msg), context))
    assert context.user_data == {}
    assert "cancelled" in msg.sent[0].lower()


def test_cmd_report_uses_telegram_client_language_for_localized_prompt():
    context = FakeContext()
    update = FakeUpdate(message=FakeMessage(), language_code="ta-IN")

    asyncio.run(bot_runner.cmd_report(update, context))

    session = context.user_data["session"]
    assert session.lang == "ta"
    assert conv.PROMPTS_BY_LANG["ta"]["location"].rstrip(".") in update.message.sent[0]


def test_cmd_report_falls_back_to_english_for_unsupported_client_language():
    context = FakeContext()
    update = FakeUpdate(message=FakeMessage(), language_code="fr-FR")

    asyncio.run(bot_runner.cmd_report(update, context))

    assert context.user_data["session"].lang == "en"


# --- fisherman mode (phase 2, milestone 5) ------------------------------------

def test_fisherman_registration_verifies_known_roster_member(monkeypatch):
    monkeypatch.setattr(bot_runner, "SessionLocal", _no_op_session_local)
    monkeypatch.setattr(
        bot_runner.fisherman_service,
        "register_fisherman",
        lambda db, source, external_id, phone: (
            SimpleNamespace(id="r1", role="fisherman"),
            "Kasimedu Fishermen Welfare Cooperative",
        ),
    )
    context = FakeContext()

    async def run():
        state = await bot_runner.cmd_fisherman(FakeUpdate(message=FakeMessage()), context)
        assert state == bot_runner.FISHERMAN_PHONE
        msg = FakeMessage(contact=FakeContact("+919840012345"))
        result = await bot_runner.on_fisherman_contact(FakeUpdate(message=msg), context)
        return result, msg

    result, msg = asyncio.run(run())
    assert result == bot_runner.ConversationHandler.END
    assert any("Kasimedu Fishermen Welfare Cooperative" in text for text in msg.sent)


def test_fisherman_registration_rejects_unknown_phone_number(monkeypatch):
    monkeypatch.setattr(bot_runner, "SessionLocal", _no_op_session_local)
    monkeypatch.setattr(
        bot_runner.fisherman_service, "register_fisherman", lambda db, source, external_id, phone: (None, None)
    )
    context = FakeContext()

    async def run():
        msg = FakeMessage(contact=FakeContact("+910000000000"))
        return await bot_runner.on_fisherman_contact(FakeUpdate(message=msg), context), msg

    result, msg = asyncio.run(run())
    assert result == bot_runner.ConversationHandler.END
    assert any("cooperative's roll" in text for text in msg.sent)


def test_cmd_sea_reports_nearest_station(monkeypatch):
    monkeypatch.setattr(bot_runner, "SessionLocal", _no_op_session_local)
    monkeypatch.setattr(
        bot_runner.fisherman_service,
        "nearest_station_reading",
        lambda db: {
            "station_id": "ndbc-46026", "station_name": "NDBC Buoy 46026", "distance_km": 12000.0,
            "is_local": False, "latest": {"wave_height": {"value": 1.2, "time": "2026-07-18T00:00:00+00:00"}},
            "anomalies": [],
        },
    )
    msg = FakeMessage()

    asyncio.run(bot_runner.cmd_sea(FakeUpdate(message=msg), FakeContext()))

    assert any("NDBC Buoy 46026" in text for text in msg.sent)
    assert any("far from this pilot area" in text for text in msg.sent)


def test_cmd_sea_reports_no_stations_configured(monkeypatch):
    monkeypatch.setattr(bot_runner, "SessionLocal", _no_op_session_local)
    monkeypatch.setattr(bot_runner.fisherman_service, "nearest_station_reading", lambda db: None)
    msg = FakeMessage()

    asyncio.run(bot_runner.cmd_sea(FakeUpdate(message=msg), FakeContext()))

    assert any("No instrument stations" in text for text in msg.sent)


def test_cmd_pfz_lists_active_zones(monkeypatch):
    monkeypatch.setattr(bot_runner, "SessionLocal", _no_op_session_local)
    monkeypatch.setattr(
        bot_runner.fisherman_service,
        "active_pfz_advisories",
        lambda db: [{"lat": 13.1, "lon": 80.4, "depth_m": 45.0, "distance_km": 30.0,
                     "bearing": "30.0 km NE of Kasimedu", "valid_until": "2026-07-20T00:00:00+00:00"}],
    )
    msg = FakeMessage()

    asyncio.run(bot_runner.cmd_pfz(FakeUpdate(message=msg), FakeContext()))

    assert any("Kasimedu" in text for text in msg.sent)


def test_cmd_pfz_reports_no_active_advisory(monkeypatch):
    monkeypatch.setattr(bot_runner, "SessionLocal", _no_op_session_local)
    monkeypatch.setattr(bot_runner.fisherman_service, "active_pfz_advisories", lambda db: [])
    msg = FakeMessage()

    asyncio.run(bot_runner.cmd_pfz(FakeUpdate(message=msg), FakeContext()))

    assert any("No potential fishing zone" in text for text in msg.sent)
