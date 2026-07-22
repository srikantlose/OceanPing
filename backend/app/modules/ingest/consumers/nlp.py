"""NLP consumer: reports.raw -> classify + embed -> reports.classified
(phase 3, milestone 8 — the bus pipeline mode).

Run as its own process, exactly like the delivery worker and IoT bridge:
    python -m app.modules.ingest.consumers.nlp

The only one of the three pipeline consumers that lazy-loads the sentence-
transformer model (nlp/classifier.py::_load_model) — so it's the only one
that can meaningfully lag under load. That's exactly the "analytics
consumer, defer under load-shed" piece core/scheduler.py's lag check exists
for (see modules/ingest/bus.py::lag, which watches this consumer group).
"""
import logging

from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models import Report
from app.modules.ingest import bus
from app.modules.nlp import classifier

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

GROUP_ID = "nlp"


def process_one(db: Session, report_id: str) -> None:
    report = db.get(Report, report_id)
    if report is None or report.processing_stage != "queued":
        return  # already handled by a prior (redelivered) attempt, or not ours yet
    classification = classifier.classify(report.text)
    if not report.hazard_locked:
        report.hazard_type = classification.hazard_type or "other"
        report.hazard_locked = True
    embedding = classification.embedding
    if embedding is None and report.text:
        embedding = classifier.embed(report.text)
    report.embedding = embedding
    report.confidence_components = {**(report.confidence_components or {}), "nlp_mode": classification.mode}
    report.processing_stage = "classified"
    db.commit()
    bus.produce(bus.TOPIC_CLASSIFIED, report_id)


def _handle(report_id: str) -> None:
    db = SessionLocal()
    try:
        process_one(db, report_id)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    log.info("NLP consumer starting")
    bus.consume_forever(GROUP_ID, bus.TOPIC_RAW, _handle)


if __name__ == "__main__":
    main()
