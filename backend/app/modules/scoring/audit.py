"""Append-only, hash-chained audit log. Every confidence change, status
transition, verification, and anomaly event lands here so any alert decision
can be reconstructed and defended after the fact."""
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog

GENESIS_HASH = "0" * 64


def canonical_envelope(event_type: str, subject_type: str, subject_id: str, payload: dict) -> str:
    return json.dumps(
        {
            "event_type": event_type,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def compute_hash(prev_hash: str, envelope: str) -> str:
    return hashlib.sha256((prev_hash + envelope).encode("utf-8")).hexdigest()


def append_audit(
    db: Session, event_type: str, subject_type: str, subject_id: str, payload: dict
) -> AuditLog:
    """Append one chained entry. Locks the chain tail to serialize writers.
    Flushes but does not commit — joins the caller's transaction."""
    tail = db.scalars(
        select(AuditLog).order_by(AuditLog.id.desc()).limit(1).with_for_update()
    ).first()
    prev_hash = tail.hash if tail else GENESIS_HASH
    envelope = canonical_envelope(event_type, subject_type, subject_id, payload)
    entry = AuditLog(
        event_type=event_type,
        subject_type=subject_type,
        subject_id=str(subject_id),
        payload=payload,
        prev_hash=prev_hash,
        hash=compute_hash(prev_hash, envelope),
    )
    db.add(entry)
    db.flush()
    return entry


def verify_chain(db: Session) -> tuple[bool, int]:
    """Walk the full chain; returns (intact, entries_checked)."""
    prev_hash = GENESIS_HASH
    count = 0
    for entry in db.scalars(select(AuditLog).order_by(AuditLog.id.asc())):
        envelope = canonical_envelope(
            entry.event_type, entry.subject_type, entry.subject_id, entry.payload
        )
        if entry.prev_hash != prev_hash or entry.hash != compute_hash(prev_hash, envelope):
            return False, count
        prev_hash = entry.hash
        count += 1
    return True, count
