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
