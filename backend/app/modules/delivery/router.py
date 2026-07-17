"""Public subscribe endpoints for channels beyond Telegram (Telegram has its
own bot conversation flow — see modules/ingest/bot_runner.py). Both endpoints
here upsert the same Subscription row the delivery worker fans alerts out to.
"""
import hashlib

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.models import Subscription
from app.modules.alerts.geofence import cells_around

router = APIRouter(tags=["delivery"])


def _lang(lang: str) -> str:
    return lang if lang in ("en", "hi", "ta") else "en"


@router.get("/subscribe/vapid-public-key")
def vapid_public_key() -> dict:
    return {"key": get_settings().vapid_public_key}


class WebPushKeys(BaseModel):
    p256dh: str
    auth: str


class WebPushSubscribeIn(BaseModel):
    lat: float
    lon: float
    endpoint: str
    keys: WebPushKeys
    lang: str = "en"


class WebPushUnsubscribeIn(BaseModel):
    endpoint: str


@router.post("/subscribe/web-push")
def subscribe_web_push(body: WebPushSubscribeIn, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    address = hashlib.sha256(body.endpoint.encode()).hexdigest()
    sub = db.scalar(
        select(Subscription).where(Subscription.channel == "web_push").where(Subscription.address == address)
    )
    if sub is None:
        sub = Subscription(channel="web_push", address=address)
        db.add(sub)
    sub.h3_cells = cells_around(body.lat, body.lon, settings.subscription_radius_rings)
    sub.lang = _lang(body.lang)
    sub.meta = {"push_subscription": {"endpoint": body.endpoint, "keys": body.keys.model_dump()}}
    db.commit()
    return {"status": "subscribed"}


@router.post("/unsubscribe/web-push")
def unsubscribe_web_push(body: WebPushUnsubscribeIn, db: Session = Depends(get_db)) -> dict:
    address = hashlib.sha256(body.endpoint.encode()).hexdigest()
    n = db.query(Subscription).filter_by(channel="web_push", address=address).delete()
    db.commit()
    return {"unsubscribed": bool(n)}


class SmsSubscribeIn(BaseModel):
    lat: float
    lon: float
    phone: str = Field(min_length=8, max_length=20)
    lang: str = "en"


class SmsUnsubscribeIn(BaseModel):
    phone: str


@router.post("/subscribe/sms")
def subscribe_sms(body: SmsSubscribeIn, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    sub = db.scalar(
        select(Subscription).where(Subscription.channel == "sms").where(Subscription.address == body.phone)
    )
    if sub is None:
        sub = Subscription(channel="sms", address=body.phone)
        db.add(sub)
    sub.h3_cells = cells_around(body.lat, body.lon, settings.subscription_radius_rings)
    sub.lang = _lang(body.lang)
    db.commit()
    return {"status": "subscribed"}


@router.post("/unsubscribe/sms")
def unsubscribe_sms(body: SmsUnsubscribeIn, db: Session = Depends(get_db)) -> dict:
    n = db.query(Subscription).filter_by(channel="sms", address=body.phone).delete()
    db.commit()
    return {"unsubscribed": bool(n)}
