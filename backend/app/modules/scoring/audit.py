"""Append-only, hash-chained audit log. Every confidence change, status
transition, verification, and anomaly event lands here so any alert decision
can be reconstructed and defended after the fact."""
import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditLog

GENESIS_HASH = "0" * 64

# Arbitrary fixed key for a Postgres transaction-scoped advisory lock (see
# append_audit) — any bigint works, it just needs to be the same constant
# every caller uses.
AUDIT_CHAIN_LOCK_KEY = 8_921_034_751


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
    """Append one chained entry. Flushes but does not commit — joins the
    caller's transaction.

    Serializes writers with a transaction-scoped Postgres advisory lock taken
    *before* reading the tail. The original approach here — `SELECT ... ORDER
    BY id DESC LIMIT 1 FOR UPDATE` — looked like it serialized appends but
    doesn't: under READ COMMITTED, Postgres picks which row to lock before
    blocking on it, and when the lock is released it re-checks only that same
    row rather than re-scanning for rows inserted meanwhile. So a second
    writer blocked on the old tail wakes up, still sees the old tail, and
    computes the same prev_hash as the writer that just committed — the chain
    silently forks. Two concurrent scheduler jobs (sensor and propagation
    forecast generation, which share an interval and therefore fire
    simultaneously) hit this repeatedly in practice.

    An advisory lock has no such row-identity ambiguity: it's one fixed key
    every writer contends for, so the tail read after acquiring it always
    reflects every committed append. It releases automatically at
    commit/rollback and so can't leak across requests.

    The lock is held from the first append in a transaction until that
    transaction commits, which does serialize long multi-append jobs (e.g.
    rescore_recent) against each other. That's inherent to a hash chain —
    prev_hash can only reference a *committed* entry — and is the right
    trade at this scale: appends are small, and a forked chain is
    unrecoverable in a way a few milliseconds of lock wait is not.

    Defence in depth: `audit_log.prev_hash` also carries a UNIQUE index (see
    models.py), so even if this locking were ever bypassed or wrong again,
    the second writer's INSERT fails loudly instead of persisting a fork."""
    db.execute(select(func.pg_advisory_xact_lock(AUDIT_CHAIN_LOCK_KEY)))
    tail = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).first()
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
