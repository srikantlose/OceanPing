"""MQTT -> sensor_readings bridge for the LoRaWAN/IoT pilot (phase 3, milestone 6).

Runs as its own process, exactly like the delivery worker:
    python -m app.modules.iot.bridge

Subscribes to the pilot's telemetry topic on the EMQX broker and, for each
message, parses it (parser.py) and writes it through the shared sensor path
(service.py). It holds no state of its own — the broker owns the queue, the
database owns everything else — so it can be killed and restarted freely, and
running a second one would just share the subscription.

Every message is handled defensively: a malformed payload from one flaky node
is logged and dropped, never allowed to take the bridge down or block other
nodes' data.
"""
import json
import logging
import time

import paho.mqtt.client as mqtt

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.modules.iot.parser import IotMessageError, parse_telemetry
from app.modules.iot.service import ingest_telemetry

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

_RECONNECT_DELAY_SECONDS = 5


def handle_message(topic: str, raw_payload: bytes) -> None:
    """Parse and ingest one message. Isolated from the paho callback so it can
    be unit-tested (and so any failure is contained to this one message)."""
    settings = get_settings()
    try:
        payload = json.loads(raw_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning("Dropping non-JSON telemetry on %s: %s", topic, exc)
        return
    try:
        tel = parse_telemetry(topic, payload, max_future_skew_minutes=settings.iot_max_future_skew_minutes)
    except IotMessageError as exc:
        log.warning("Dropping invalid telemetry on %s: %s", topic, exc)
        return

    db = SessionLocal()
    try:
        ingest_telemetry(db, tel)
    except IotMessageError as exc:
        # e.g. a first-ever message from a node with no location.
        log.warning("Cannot ingest telemetry from %s: %s", tel.node_id, exc)
        db.rollback()
    except Exception:
        log.exception("Failed to ingest telemetry from %s", tel.node_id)
        db.rollback()
    finally:
        db.close()


def _on_connect(client, userdata, flags, reason_code, properties=None):
    settings = get_settings()
    if reason_code == 0:
        client.subscribe(settings.mqtt_topic)
        log.info("IoT bridge connected, subscribed to %s", settings.mqtt_topic)
    else:
        log.error("IoT bridge failed to connect: reason_code=%s", reason_code)


def _on_message(client, userdata, msg):
    handle_message(msg.topic, msg.payload)


def build_client() -> mqtt.Client:
    settings = get_settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=settings.mqtt_client_id)
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password or None)
    client.on_connect = _on_connect
    client.on_message = _on_message
    return client


def main() -> None:
    settings = get_settings()
    client = build_client()
    log.info("IoT bridge starting, broker %s:%s", settings.mqtt_host, settings.mqtt_port)
    while True:
        try:
            client.connect(settings.mqtt_host, settings.mqtt_port)
            client.loop_forever()
        except Exception:
            log.exception("IoT bridge connection lost; retrying in %ss", _RECONNECT_DELAY_SECONDS)
            time.sleep(_RECONNECT_DELAY_SECONDS)


if __name__ == "__main__":
    main()
