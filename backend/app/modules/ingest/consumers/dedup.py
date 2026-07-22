"""Dedup consumer: reports.classified -> assign_incident -> reports.assigned
(phase 3, milestone 8 — the bus pipeline mode).

Run as its own process:
    python -m app.modules.ingest.consumers.dedup

No ML dependency — assign_incident's semantic check simply degrades to a
spatial-only merge when an embedding isn't there yet (the same fallback
nlp/dedup.py::should_merge already had for text-free reports), so this
consumer never lags behind the nlp consumer under load. core/scheduler.py's
load-shed check watches the nlp consumer group specifically because this one
and the scoring consumer are never the bottleneck.
"""
import logging

from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models import Report
from app.modules.ingest import bus
from app.modules.ingest.service import audit_report_created
from app.modules.nlp.dedup import assign_incident

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

GROUP_ID = "dedup"


def process_one(db: Session, report_id: str) -> None:
    report = db.get(Report, report_id)
    if report is None or report.processing_stage != "classified":
        return
    assign_incident(db, report)
    nlp_mode = (report.confidence_components or {}).get("nlp_mode", "n/a")
    audit_report_created(db, report, nlp_mode)
    report.processing_stage = "assigned"
    db.commit()
    bus.produce(bus.TOPIC_ASSIGNED, report_id)


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
    log.info("Dedup consumer starting")
    bus.consume_forever(GROUP_ID, bus.TOPIC_CLASSIFIED, _handle)


if __name__ == "__main__":
    main()
