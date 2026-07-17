import uuid

import redis

from app.modules.delivery import queue as delivery_queue


class FakeRedis:
    def __init__(self):
        self.pushed = []

    def rpush(self, key, value):
        self.pushed.append((key, value))

    def blpop(self, key, timeout=None):
        if not self.pushed:
            return None
        return self.pushed.pop(0)


class BrokenRedis:
    def rpush(self, *a, **k):
        raise ConnectionError("redis is down")


class ExpiredBlpopRedis:
    """Some redis-py versions raise TimeoutError on an expired BLPOP wait
    instead of returning None — same meaning, different signal."""

    def blpop(self, key, timeout=None):
        raise redis.exceptions.TimeoutError("Timeout reading from socket")


def test_enqueue_alert_pushes_stringified_id(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(delivery_queue, "get_redis", lambda: fake)
    alert_id = uuid.uuid4()
    delivery_queue.enqueue_alert(alert_id)
    assert fake.pushed == [(delivery_queue.get_settings().delivery_queue_key, str(alert_id))]


def test_enqueue_alert_never_raises_when_redis_is_down(monkeypatch):
    monkeypatch.setattr(delivery_queue, "get_redis", lambda: BrokenRedis())
    delivery_queue.enqueue_alert(uuid.uuid4())  # must not raise — issuance can't block on this


def test_dequeue_alert_returns_none_when_empty(monkeypatch):
    monkeypatch.setattr(delivery_queue, "get_redis", lambda: FakeRedis())
    assert delivery_queue.dequeue_alert(timeout=1) is None


def test_dequeue_alert_extracts_value(monkeypatch):
    fake = FakeRedis()
    fake.pushed.append((delivery_queue.get_settings().delivery_queue_key, "abc-123"))
    monkeypatch.setattr(delivery_queue, "get_redis", lambda: fake)
    assert delivery_queue.dequeue_alert(timeout=1) == "abc-123"


def test_dequeue_alert_treats_expired_blpop_timeout_as_empty(monkeypatch):
    monkeypatch.setattr(delivery_queue, "get_redis", lambda: ExpiredBlpopRedis())
    assert delivery_queue.dequeue_alert(timeout=1) is None
