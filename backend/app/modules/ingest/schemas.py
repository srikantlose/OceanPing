from datetime import datetime

from pydantic import BaseModel


class ReportOut(BaseModel):
    id: str
    status: str
    hazard_type: str
    urgency: str
    lang: str
    confidence: float
    incident_id: str | None
    created_at: datetime
    # Bus pipeline mode only (phase 3, milestone 8): "scored" immediately in
    # inline mode (the default) since there's nothing left to catch up on by
    # the time this row is ever visible. In bus mode this starts "queued" and
    # a caller who cares can poll GET /reports/{id} until it reads "scored" —
    # hazard_type/confidence/incident_id are provisional before then.
    processing_stage: str

    @classmethod
    def from_report(cls, report) -> "ReportOut":
        return cls(
            id=str(report.id),
            status=report.status,
            hazard_type=report.hazard_type,
            urgency=report.urgency,
            lang=report.lang,
            confidence=report.confidence,
            incident_id=str(report.incident_id) if report.incident_id else None,
            created_at=report.created_at,
            processing_stage=report.processing_stage,
        )
