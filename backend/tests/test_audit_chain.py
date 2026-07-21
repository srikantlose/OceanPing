from app.models import AuditLog
from app.modules.scoring import audit as audit_mod
from app.modules.scoring.audit import GENESIS_HASH, canonical_envelope, compute_hash


def _build_chain(events):
    chain = []
    prev = GENESIS_HASH
    for ev in events:
        env = canonical_envelope(**ev)
        h = compute_hash(prev, env)
        chain.append({"prev_hash": prev, "hash": h, **ev})
        prev = h
    return chain


def _chain_intact(chain):
    prev = GENESIS_HASH
    for entry in chain:
        env = canonical_envelope(
            entry["event_type"], entry["subject_type"], entry["subject_id"], entry["payload"]
        )
        if entry["prev_hash"] != prev or entry["hash"] != compute_hash(prev, env):
            return False
        prev = entry["hash"]
    return True


EVENTS = [
    {"event_type": "report.created", "subject_type": "report", "subject_id": "r1",
     "payload": {"hazard_type": "coastal_flooding"}},
    {"event_type": "report.rescored", "subject_type": "report", "subject_id": "r1",
     "payload": {"from": 0.0, "to": 0.62}},
    {"event_type": "report.status_changed", "subject_type": "report", "subject_id": "r1",
     "payload": {"from": "unverified", "to": "corroborated"}},
]


def test_chain_is_deterministic_and_intact():
    assert _build_chain(EVENTS) == _build_chain(EVENTS)
    assert _chain_intact(_build_chain(EVENTS))


def test_payload_tampering_breaks_chain():
    chain = _build_chain(EVENTS)
    chain[1]["payload"]["to"] = 0.99  # falsify a confidence value after the fact
    assert not _chain_intact(chain)


def test_reordering_breaks_chain():
    chain = _build_chain(EVENTS)
    chain[0], chain[1] = chain[1], chain[0]
    assert not _chain_intact(chain)


def test_envelope_is_key_order_independent():
    a = canonical_envelope("e", "t", "s", {"x": 1, "y": 2})
    b = canonical_envelope("e", "t", "s", {"y": 2, "x": 1})
    assert a == b


# --- concurrency: appends must serialize on the advisory lock ------------------
#
# A forked chain (two entries sharing one prev_hash) is unrecoverable after the
# fact, so the guarantees below are asserted structurally rather than left to a
# live race to expose.


class _RecordingDb:
    """Records the order of execute()/scalars() calls so the lock-before-read
    ordering append_audit depends on can be asserted without a live database."""

    def __init__(self, tail=None):
        self.calls: list[str] = []
        self.added: list = []
        self._tail = tail

    def execute(self, stmt):
        self.calls.append(str(stmt))
        return None

    def scalars(self, stmt):
        self.calls.append(str(stmt))
        return _First(self._tail)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


class _First:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


def test_append_audit_takes_the_advisory_lock_before_reading_the_tail():
    """Reading the tail first would reintroduce the original race: the read
    could land before a concurrent writer commits, and the lock would then
    serialize two writers that had already both decided on the same prev_hash."""
    db = _RecordingDb()
    audit_mod.append_audit(db, event_type="e", subject_type="t", subject_id="s", payload={})

    assert len(db.calls) >= 2
    assert "pg_advisory_xact_lock" in db.calls[0]
    assert "audit_log" in db.calls[1]


def test_append_audit_does_not_lock_the_tail_row():
    """FOR UPDATE on the tail row is what silently forked the chain — under
    READ COMMITTED a blocked writer re-checks that same row instead of
    re-scanning, so it wakes up and reuses a stale prev_hash."""
    db = _RecordingDb()
    audit_mod.append_audit(db, event_type="e", subject_type="t", subject_id="s", payload={})

    assert not any("FOR UPDATE" in c.upper() for c in db.calls)


def test_append_audit_chains_onto_the_current_tail():
    tail = AuditLog(event_type="prior", subject_type="t", subject_id="s", payload={},
                    prev_hash=GENESIS_HASH, hash="a" * 64)
    db = _RecordingDb(tail=tail)

    entry = audit_mod.append_audit(db, event_type="e", subject_type="t", subject_id="s", payload={"k": 1})

    assert entry.prev_hash == "a" * 64
    assert entry.hash == compute_hash("a" * 64, canonical_envelope("e", "t", "s", {"k": 1}))


def test_append_audit_starts_from_genesis_on_an_empty_chain():
    entry = audit_mod.append_audit(_RecordingDb(), event_type="e", subject_type="t", subject_id="s", payload={})
    assert entry.prev_hash == GENESIS_HASH


def test_prev_hash_carries_a_unique_index_so_a_fork_cannot_persist():
    """The database-level backstop behind the advisory lock: two entries
    claiming the same predecessor is exactly what a duplicate prev_hash means."""
    indexes = {ix.name: ix for ix in AuditLog.__table__.indexes}
    assert "ix_audit_log_prev_hash" in indexes
    index = indexes["ix_audit_log_prev_hash"]
    assert index.unique
    assert [c.name for c in index.columns] == ["prev_hash"]
