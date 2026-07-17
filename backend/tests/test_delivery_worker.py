from app.models import Alert, Subscription
from app.modules.delivery import worker
from app.modules.delivery.worker import _matches


def _alert(tier="watch", cells=None):
    cells = ["a", "b"] if cells is None else cells
    return Alert(hazard_type="coastal_flooding", tier=tier, h3_cells=cells, message={"en": "x"})


def _sub(min_tier="advisory", cells=None):
    cells = ["a"] if cells is None else cells
    return Subscription(channel="telegram", address="1", min_tier=min_tier, h3_cells=cells)


def test_matches_requires_geofence_overlap():
    assert _matches(_alert(cells=["a", "b"]), _sub(cells=["a"]))
    assert not _matches(_alert(cells=["a", "b"]), _sub(cells=["z"]))


def test_matches_respects_min_tier_threshold():
    assert _matches(_alert(tier="watch"), _sub(min_tier="advisory"))
    assert _matches(_alert(tier="watch"), _sub(min_tier="watch"))
    assert not _matches(_alert(tier="advisory"), _sub(min_tier="watch"))
    assert not _matches(_alert(tier="watch"), _sub(min_tier="warning"))


def test_matches_false_when_alert_has_no_cells():
    assert not _matches(_alert(cells=[]), _sub(cells=["a"]))


class _EventuallyCommittedDb:
    """Simulates the read-after-write race: a batch caller (e.g.
    rescore_recent) enqueues an alert id before its own commit, so the
    worker's first few lookups on a separate connection see nothing yet."""

    def __init__(self, misses_before_hit: int, alert: Alert):
        self.misses_before_hit = misses_before_hit
        self.alert = alert
        self.calls = 0

    def get(self, model, id):
        self.calls += 1
        if self.calls <= self.misses_before_hit:
            return None
        return self.alert


class _NeverCommittedDb:
    def get(self, model, id):
        return None


def test_load_alert_with_retries_rides_out_the_commit_race(monkeypatch):
    monkeypatch.setattr(worker.time, "sleep", lambda _: None)  # don't slow the test down
    db = _EventuallyCommittedDb(misses_before_hit=2, alert=_alert())
    assert worker._load_alert_with_retries(db, "some-id") is db.alert
    assert db.calls == 3


def test_load_alert_with_retries_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(worker.time, "sleep", lambda _: None)
    db = _NeverCommittedDb()
    assert worker._load_alert_with_retries(db, "some-id") is None
