"""Scoring consumer: reports.assigned -> rescore_report (phase 3, milestone 8
— the bus pipeline mode).

Run as its own process:
    python -m app.modules.ingest.consumers.scoring

This is where an automatic advisory/watch alert actually gets created or
upgraded (scoring/service.py::rescore_report -> alerts/service.py::
sync_incident_alert) — the "alerting" half of the plan's "ingestion +
alerting protected, defer analytics consumers" exit criterion. No ML
dependency, so it stays fast even while the nlp consumer is backed up.
"""
import logging

from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models import Report
from app.modules.ingest import bus
from app.modules.scoring.service import rescore_report

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

GROUP_ID = "scoring"


def process_one(db: Session, report_id: str) -> None:
    report = db.get(Report, report_id)
    if report is None or report.processing_stage != "assigned":
        return
    rescore_report(db, report)
    report.processing_stage = "scored"
    db.commit()


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
    log.info("Scoring consumer starting")
    bus.consume_forever(GROUP_ID, bus.TOPIC_ASSIGNED, _handle)


if __name__ == "__main__":
    main()
