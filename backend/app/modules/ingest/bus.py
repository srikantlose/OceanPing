"""Redpanda (Kafka-API) plumbing for the "bus" pipeline mode (phase 3,
milestone 8 — the architecture split).

Every message is just a report id, never a payload — the bus is a durable,
ordered wake-up signal, the same "queue holds an id, worker looks it up" idiom
delivery/queue.py already uses for alerts (there via Redis; here via a real
topic, since multiple independent consumer groups need to each read the full
backlog at their own pace, which a single Redis list can't do). The database
row stays the single source of truth, which is what makes every consumer
naturally idempotent: reprocessing a redelivered id just re-reads current
state and no-ops if the expected processing_stage has already moved past it.

Three topics chain the pipeline in the same order create_report() used to run
inline: reports.raw -> (nlp consumer) -> reports.classified -> (dedup
consumer) -> reports.assigned -> (scoring consumer), which is also where an
automatic alert gets created/upgraded (see scoring/service.py::rescore_report
-> alerts/service.py::sync_incident_alert). Only used when
settings.pipeline_mode == "bus"; inline mode never imports this module.
"""
import logging
import threading

from confluent_kafka import Consumer, KafkaError, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic

from app.core.config import get_settings

log = logging.getLogger(__name__)

TOPIC_RAW = "reports.raw"
TOPIC_CLASSIFIED = "reports.classified"
TOPIC_ASSIGNED = "reports.assigned"
ALL_TOPICS = (TOPIC_RAW, TOPIC_CLASSIFIED, TOPIC_ASSIGNED)

_producer_lock = threading.Lock()
_producer: Producer | None = None


def get_producer() -> Producer:
    global _producer
    if _producer is None:
        with _producer_lock:
            if _producer is None:
                _producer = Producer({"bootstrap.servers": get_settings().kafka_bootstrap_servers})
    return _producer


def ensure_topics(num_partitions: int = 3) -> None:
    """Idempotent topic creation, so a fresh Redpanda container doesn't rely
    on auto-create (off by default on some brokers, and racy under the
    50x-scale drill's burst of near-simultaneous first produces)."""
    admin = AdminClient({"bootstrap.servers": get_settings().kafka_bootstrap_servers})
    existing = admin.list_topics(timeout=10).topics
    missing = [t for t in ALL_TOPICS if t not in existing]
    if not missing:
        return
    futures = admin.create_topics(
        [NewTopic(t, num_partitions=num_partitions, replication_factor=1) for t in missing]
    )
    for topic, future in futures.items():
        try:
            future.result()
            log.info("Created topic %s", topic)
        except Exception as exc:
            # TOPIC_ALREADY_EXISTS under a concurrent creator racing us — fine.
            log.warning("Topic creation for %s: %s", topic, exc)


def produce(topic: str, report_id: str) -> None:
    producer = get_producer()
    producer.produce(topic, key=report_id.encode(), value=report_id.encode())
    producer.flush(timeout=10)


def build_consumer(group_id: str) -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": get_settings().kafka_bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )


def consume_forever(group_id: str, topic: str, handle) -> None:
    """Poll loop shared by every consumer entrypoint (nlp/dedup/scoring). No
    shared state beyond the broker's own offsets, so any of them can be
    killed and restarted freely, same as the delivery worker and IoT bridge.

    `handle(report_id)` must be idempotent — the offset is only committed
    after it returns without raising, so a crash mid-message gets the same
    message redelivered rather than silently dropped, and a message whose
    report already moved past the expected stage (a redelivery, or a stage
    this consumer doesn't own) is expected to no-op harmlessly.
    """
    consumer = build_consumer(group_id)
    consumer.subscribe([topic])
    log.info("Consumer %s subscribed to %s", group_id, topic)
    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                log.error("Consumer error on %s: %s", topic, msg.error())
                continue
            report_id = msg.value().decode()
            try:
                handle(report_id)
            except Exception:
                log.exception("Failed handling report %s from %s; will retry", report_id, topic)
                continue
            consumer.commit(msg)
    finally:
        consumer.close()


def lag(group_id: str, topic: str) -> int:
    """Total consumer-group lag across every partition of `topic` — a real
    backlog signal (not synthetic), used by core/scheduler.py's load-shed
    check to decide whether an analytics job should skip a tick. Best-effort:
    any broker hiccup reads as zero lag (never blocks/sheds on a metadata
    error), the same fail-open posture the rate limiter uses when Redis is
    unreachable (ingest/service.py::_check_rate_limits).
    """
    consumer = Consumer(
        {
            "bootstrap.servers": get_settings().kafka_bootstrap_servers,
            "group.id": group_id,
            "enable.auto.commit": False,
        }
    )
    try:
        cluster_md = consumer.list_topics(topic, timeout=5.0)
        topic_md = cluster_md.topics.get(topic)
        if topic_md is None or topic_md.error is not None:
            return 0
        partitions = [TopicPartition(topic, p) for p in topic_md.partitions]
        committed = consumer.committed(partitions, timeout=5.0)
        total = 0
        for tp in committed:
            low, high = consumer.get_watermark_offsets(tp, timeout=5.0, cached=False)
            offset = tp.offset if tp.offset >= 0 else low
            total += max(0, high - offset)
        return total
    except Exception:
        log.warning("Could not compute consumer lag for group=%s topic=%s", group_id, topic, exc_info=True)
        return 0
    finally:
        consumer.close()
