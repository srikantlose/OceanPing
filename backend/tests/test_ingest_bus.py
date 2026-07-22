"""Redpanda plumbing (phase 3, milestone 8) tested against fakes for
confluent_kafka's Producer/Consumer/AdminClient — no broker needed, same
"unit-test the pure/isolatable logic, drill-verify the rest live" split this
project already draws for ingest/service.py (see test_offline_sync.py's
comment on why create_report() itself has no fake-DB unit test)."""
from types import SimpleNamespace

import pytest

from app.modules.ingest import bus


class _FakeProducer:
    def __init__(self):
        self.produced = []

    def produce(self, topic, key=None, value=None):
        self.produced.append((topic, key, value))

    def flush(self, timeout=None):
        pass


def test_produce_sends_report_id_as_both_key_and_value(monkeypatch):
    fake = _FakeProducer()
    monkeypatch.setattr(bus, "_producer", fake)
    bus.produce("reports.raw", "abc-123")
    assert fake.produced == [("reports.raw", b"abc-123", b"abc-123")]


# --- consume_forever: only commits after a successful handle() -----------------


class _FakeMsg:
    def __init__(self, value, err=None):
        self._value = value
        self._err = err

    def value(self):
        return self._value.encode()

    def error(self):
        return self._err


class _FakeKafkaErr:
    def __init__(self, code):
        self._code = code

    def code(self):
        return self._code


class _FakeConsumer:
    def __init__(self, messages):
        self._queue = list(messages)
        self.committed_msgs = []
        self.closed = False

    def subscribe(self, topics):
        self.subscribed = topics

    def poll(self, timeout):
        if self._queue:
            return self._queue.pop(0)
        raise KeyboardInterrupt  # stop the test's otherwise-infinite loop

    def commit(self, msg):
        self.committed_msgs.append(msg)

    def close(self):
        self.closed = True


def test_consume_forever_commits_only_after_successful_handle(monkeypatch):
    ok_msg = _FakeMsg("report-1")
    bad_msg = _FakeMsg("report-2")
    fake = _FakeConsumer([ok_msg, bad_msg])
    monkeypatch.setattr(bus, "build_consumer", lambda group_id: fake)

    handled = []

    def handle(report_id):
        handled.append(report_id)
        if report_id == "report-2":
            raise RuntimeError("boom")

    with pytest.raises(KeyboardInterrupt):
        bus.consume_forever("g", "reports.raw", handle)

    assert handled == ["report-1", "report-2"]
    # report-2's handler raised, so its offset must never be committed —
    # that's what makes redelivery-and-retry safe.
    assert fake.committed_msgs == [ok_msg]
    assert fake.closed


def test_consume_forever_skips_partition_eof_without_committing(monkeypatch):
    eof_msg = _FakeMsg(None, err=_FakeKafkaErr(bus.KafkaError._PARTITION_EOF))
    real_msg = _FakeMsg("report-1")
    fake = _FakeConsumer([eof_msg, real_msg])
    monkeypatch.setattr(bus, "build_consumer", lambda group_id: fake)

    handled = []
    with pytest.raises(KeyboardInterrupt):
        bus.consume_forever("g", "reports.raw", handled.append)

    assert handled == ["report-1"]


# --- lag(): fail-open to zero on any broker trouble -----------------------------


class _FakeTopicMD:
    def __init__(self, partitions, error=None):
        self.partitions = partitions
        self.error = error


class _FakeClusterMD:
    def __init__(self, topics):
        self.topics = topics


class _FakeTP:
    def __init__(self, topic, partition):
        self.topic = topic
        self.partition = partition
        self.offset = -1


class _FakeLagConsumer:
    def __init__(self, conf):
        pass

    def list_topics(self, topic, timeout=None):
        return _FakeClusterMD({topic: _FakeTopicMD([0, 1])})

    def committed(self, partitions, timeout=None):
        for tp in partitions:
            tp.offset = 100
        return partitions

    def get_watermark_offsets(self, tp, timeout=None, cached=False):
        return (0, 150)

    def close(self):
        pass


def test_lag_sums_backlog_across_partitions(monkeypatch):
    monkeypatch.setattr(bus, "Consumer", _FakeLagConsumer)
    monkeypatch.setattr(bus, "TopicPartition", _FakeTP)
    assert bus.lag("nlp", "reports.raw") == 100  # (150-100) x 2 partitions


class _NoTopicConsumer:
    def __init__(self, conf):
        pass

    def list_topics(self, topic, timeout=None):
        return _FakeClusterMD({})

    def close(self):
        pass


def test_lag_is_zero_when_topic_does_not_exist_yet(monkeypatch):
    monkeypatch.setattr(bus, "Consumer", _NoTopicConsumer)
    assert bus.lag("nlp", "reports.raw") == 0


class _ExplodingConsumer:
    def __init__(self, conf):
        pass

    def list_topics(self, topic, timeout=None):
        raise RuntimeError("no broker reachable")

    def close(self):
        pass


def test_lag_fails_open_to_zero_on_broker_error(monkeypatch):
    monkeypatch.setattr(bus, "Consumer", _ExplodingConsumer)
    assert bus.lag("nlp", "reports.raw") == 0


# --- ensure_topics(): only creates what's missing -------------------------------


class _FakeFuture:
    def result(self):
        return None


class _FakeAdmin:
    def __init__(self, conf):
        self.created = []

    def list_topics(self, timeout=None):
        return SimpleNamespace(topics={"reports.raw": object()})

    def create_topics(self, new_topics):
        self.created = [t.topic for t in new_topics]
        return {t.topic: _FakeFuture() for t in new_topics}


def test_ensure_topics_creates_only_the_missing_ones(monkeypatch):
    fake_admin = _FakeAdmin(None)
    monkeypatch.setattr(bus, "AdminClient", lambda conf: fake_admin)
    bus.ensure_topics()
    assert set(fake_admin.created) == {"reports.classified", "reports.assigned"}
