"""Redis-backed delivery queue: alert issuance pushes an alert id, the worker
blocks on popping them. Enqueue is best-effort — a Redis outage must never
block alert issuance or report scoring."""
import logging
import uuid

import redis

from app.core.config import get_settings
from app.core.redisclient import get_redis

log = logging.getLogger(__name__)


def enqueue_alert(alert_id: uuid.UUID) -> None:
    try:
        get_redis().rpush(get_settings().delivery_queue_key, str(alert_id))
    except Exception:
        log.warning("Could not enqueue alert %s for delivery; worker will miss it", alert_id, exc_info=True)


def dequeue_alert(timeout: int | None = None) -> str | None:
    settings = get_settings()
    try:
        result = get_redis().blpop(
            settings.delivery_queue_key,
            timeout=timeout if timeout is not None else settings.delivery_queue_timeout_seconds,
        )
    except redis.exceptions.TimeoutError:
        # Some redis-py versions surface an expired BLPOP wait as a client-side
        # TimeoutError instead of returning None — same meaning either way.
        return None
    if result is None:
        return None
    _, alert_id = result
    return alert_id
