"""Twilio/Exotel Voice webhook — form-encoded POST at each call step, TwiML
response out. See modules/ivr/service.py for the flow and why it's structured
this way. Public (no analyst auth): the only real caller is the telephony
provider, identified by knowing this URL, same trust model Twilio/Exotel
assume for their own webhooks.
"""
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.ivr import service

router = APIRouter(tags=["ivr"])


@router.post("/webhooks/ivr/voice")
async def voice_webhook(request: Request, db: Session = Depends(get_db)) -> Response:
    step = request.query_params.get("step", "start")
    form = await request.form()
    call_sid = form.get("CallSid", "")
    from_number = form.get("From", "")

    if step == "language":
        twiml = service.handle_language(call_sid, form.get("Digits", ""))
    elif step == "hazard":
        twiml = service.handle_hazard(call_sid, form.get("Digits", ""))
    elif step == "location":
        twiml = service.handle_location(call_sid, form.get("Digits", ""))
    elif step == "recording":
        twiml = service.handle_recording(db, call_sid, from_number, form.get("RecordingUrl"))
    else:
        twiml = service.handle_start()

    return Response(content=twiml, media_type="text/xml")
