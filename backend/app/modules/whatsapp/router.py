"""Meta WhatsApp Business Cloud API webhook — GET verifies the webhook
subscription challenge, POST receives inbound messages. Both are public (no
analyst auth), same trust boundary as the Telegram bot and /chat: the real
gate here is the X-Hub-Signature-256 check in verify_signature(), not a
session.
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.modules.whatsapp import client, service

log = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"])


@router.get("/webhooks/whatsapp")
def verify_webhook(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
) -> Response:
    settings = get_settings()
    if hub_mode == "subscribe" and settings.whatsapp_verify_token and hub_verify_token == settings.whatsapp_verify_token:
        return Response(content=hub_challenge, media_type="text/plain")
    return Response(status_code=403)


@router.post("/webhooks/whatsapp")
async def receive_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    raw_body = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    if not client.verify_signature(raw_body, signature):
        log.warning("Rejected WhatsApp webhook: bad signature")
        raise HTTPException(status_code=403, detail="invalid signature")
    payload = json.loads(raw_body) if raw_body else {}
    service.handle_payload(db, payload)
    return {"status": "ok"}
