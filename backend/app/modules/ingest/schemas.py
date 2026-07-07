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
        )
