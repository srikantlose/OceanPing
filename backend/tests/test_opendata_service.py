import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from app.modules.geo.h3utils import cell_centroid, cell_for, cell_to_parent
from app.modules.opendata import service


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Db:
    """Fake DB double, same style as test_recovery_service.py's — scalars()/
    execute() queued in call order, get()/add() backed by a plain dict."""

    def __init__(self, store=None):
        self._scalars_queue: list[list] = []
        self._execute_queue: list[list] = []
        self._store = dict(store or {})
        self.added: list = []
        self.committed = False

    def queue_scalars(self, rows):
        self._scalars_queue.append(rows)

    def queue_execute(self, rows):
        self._execute_queue.append(rows)

    def scalars(self, stmt):
        return _Rows(self._scalars_queue.pop(0))

    def execute(self, stmt):
        return _Rows(self._execute_queue.pop(0))

    def get(self, model, pk):
        return self._store.get(pk)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.committed = True


def _no_audit(monkeypatch):
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: None)


# --- API keys --------------------------------------------------------------


def test_create_api_key_only_persists_a_hash(monkeypatch):
    _no_audit(monkeypatch)
    db = _Db()

    row, raw_key = service.create_api_key(db, label="Test University", created_by="alice")

    assert raw_key.startswith(service.API_KEY_PREFIX)
    assert row.key_hash == hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    assert row.key_hash != raw_key
    assert row.key_prefix != raw_key and len(row.key_prefix) < len(raw_key)  # display prefix, not the full secret
    assert row in db.added
    assert db.committed


def test_create_api_key_audits_the_label_not_the_raw_key(monkeypatch):
    captured = {}
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: captured.update(kw))
    db = _Db()

    row, raw_key = service.create_api_key(db, label="Test University", created_by="alice")

    assert captured["event_type"] == "opendata.api_key_created"
    assert captured["payload"]["label"] == "Test University"
    assert raw_key not in str(captured["payload"])


def test_verify_api_key_accepts_the_matching_hash(monkeypatch):
    raw_key = "op_live_testkey123"
    row = SimpleNamespace(
        key_hash=hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
        revoked_at=None,
        last_used_at=None,
    )
    db = _Db()
    db.queue_scalars([row])

    result = service.verify_api_key(db, raw_key)

    assert result is row
    assert row.last_used_at is not None
    assert db.committed


def test_verify_api_key_rejects_an_unknown_key(monkeypatch):
    db = _Db()
    db.queue_scalars([])

    assert service.verify_api_key(db, "op_live_unknown") is None


def test_verify_api_key_rejects_a_revoked_key(monkeypatch):
    raw_key = "op_live_testkey123"
    row = SimpleNamespace(
        key_hash=hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
        revoked_at=datetime.now(timezone.utc),
        last_used_at=None,
    )
    db = _Db()
    db.queue_scalars([row])

    assert service.verify_api_key(db, raw_key) is None


def test_revoke_api_key_sets_timestamp_and_audits(monkeypatch):
    captured = {}
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: captured.update(kw))
    row = SimpleNamespace(id="k1", revoked_at=None)
    db = _Db(store={"k1": row})

    result = service.revoke_api_key(db, "k1", revoked_by="alice")

    assert result is row
    assert row.revoked_at is not None
    assert captured["event_type"] == "opendata.api_key_revoked"


def test_revoke_api_key_is_a_no_op_for_an_already_revoked_key(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: calls.append(kw))
    row = SimpleNamespace(id="k1", revoked_at=datetime.now(timezone.utc))
    db = _Db(store={"k1": row})

    result = service.revoke_api_key(db, "k1", revoked_by="alice")

    assert result is row
    assert calls == []


def test_revoke_api_key_returns_none_for_an_unknown_key(monkeypatch):
    assert service.revoke_api_key(_Db(), "nope", revoked_by="alice") is None


# --- rate limiting -----------------------------------------------------------


class _FakeRedis:
    def __init__(self, start=0, fail=False):
        self.count = start
        self.fail = fail

    def incr(self, key):
        if self.fail:
            raise ConnectionError("redis unreachable")
        self.count += 1
        return self.count

    def expire(self, key, seconds):
        pass


def test_check_rate_limit_allows_calls_at_or_under_the_cap(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(open_data_rate_limit_per_hour=3))
    fake = _FakeRedis()
    monkeypatch.setattr(service, "get_redis", lambda: fake)

    for _ in range(3):
        service.check_rate_limit("key-1")  # counts reach 1, 2, 3 — none exceed the cap of 3


def test_check_rate_limit_raises_once_over_the_cap(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(open_data_rate_limit_per_hour=3))
    fake = _FakeRedis()
    monkeypatch.setattr(service, "get_redis", lambda: fake)

    for _ in range(3):
        service.check_rate_limit("key-1")
    with pytest.raises(service.RateLimited):
        service.check_rate_limit("key-1")


def test_check_rate_limit_fails_open_when_redis_is_unavailable(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(open_data_rate_limit_per_hour=1))
    monkeypatch.setattr(service, "get_redis", lambda: _FakeRedis(fail=True))

    service.check_rate_limit("key-1")  # doesn't raise — logs a warning and skips instead


# --- DP / k-anonymity aggregation --------------------------------------------


def test_laplace_noise_is_zero_centered_and_smaller_epsilon_means_more_noise():
    rng_private = np.random.default_rng(42)
    rng_loose = np.random.default_rng(42)
    private_samples = [service._laplace_noise(0.1, rng_private) for _ in range(2000)]
    loose_samples = [service._laplace_noise(10.0, rng_loose) for _ in range(2000)]

    assert abs(np.mean(private_samples)) < 5.0
    assert np.std(private_samples) > np.std(loose_samples)


def test_aggregate_events_suppresses_groups_below_the_k_anonymity_floor(monkeypatch):
    monkeypatch.setattr(service, "_laplace_noise", lambda epsilon, rng: 0.0)
    now = datetime.now(timezone.utc)
    cell = cell_for(13.08, 80.27)
    db = _Db()
    db.queue_execute(
        [(cell, "storm_surge", now)] * 3    # below k=5 -> suppressed
        + [(cell, "tsunami", now)] * 10      # at/above k=5 -> released
    )

    result = service.aggregate_events(
        db, now - timedelta(days=1), now + timedelta(days=1),
        h3_resolution=6, k_anonymity_min=5, dp_epsilon=1.0,
    )

    assert result["suppressed_group_count"] == 1
    assert result["row_count"] == 1
    row = result["rows"][0]
    assert row["hazard_type"] == "tsunami"
    assert row["report_count"] == 10  # noise forced to 0 above
    assert row["h3_cell"] == cell_to_parent(cell, 6)


def test_aggregate_events_coarsens_to_the_requested_h3_resolution(monkeypatch):
    monkeypatch.setattr(service, "_laplace_noise", lambda epsilon, rng: 0.0)
    now = datetime.now(timezone.utc)
    cell = cell_for(13.08, 80.27)
    parent = cell_to_parent(cell, 6)
    centroid_lat, centroid_lon = cell_centroid(parent)
    db = _Db()
    db.queue_execute([(cell, "tsunami", now)] * 5)

    result = service.aggregate_events(
        db, now - timedelta(days=1), now + timedelta(days=1),
        h3_resolution=6, k_anonymity_min=5, dp_epsilon=1.0,
    )

    row = result["rows"][0]
    assert row["h3_cell"] == parent
    assert row["centroid_lat"] == pytest.approx(centroid_lat, abs=1e-4)
    assert row["centroid_lon"] == pytest.approx(centroid_lon, abs=1e-4)


def test_aggregate_events_returns_no_rows_when_nothing_meets_the_floor(monkeypatch):
    now = datetime.now(timezone.utc)
    cell = cell_for(13.08, 80.27)
    db = _Db()
    db.queue_execute([(cell, "tsunami", now)] * 2)

    result = service.aggregate_events(
        db, now - timedelta(days=1), now + timedelta(days=1),
        h3_resolution=6, k_anonymity_min=5, dp_epsilon=1.0,
    )

    assert result["rows"] == []
    assert result["suppressed_group_count"] == 1


def test_build_dataset_release_persists_a_checksummed_snapshot_and_audits(monkeypatch):
    monkeypatch.setattr(service, "_laplace_noise", lambda epsilon, rng: 0.0)
    captured = {}
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: captured.update(kw))
    now = datetime.now(timezone.utc)
    cell = cell_for(13.08, 80.27)
    db = _Db()
    db.queue_execute([(cell, "tsunami", now)] * 6)

    release = service.build_dataset_release(
        db, now - timedelta(days=1), now + timedelta(days=1), created_by="alice"
    )

    assert release in db.added
    assert release.row_count == 1
    assert release.suppressed_group_count == 0
    assert len(release.checksum) == 64  # sha256 hex digest length
    assert db.committed
    assert captured["event_type"] == "opendata.dataset_released"
    assert captured["payload"]["row_count"] == 1
    assert captured["payload"]["checksum"] == release.checksum


# --- retention / anonymization job -------------------------------------------


def test_anonymize_expired_reports_overwrites_lat_lon_geom_for_old_reports(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(open_data_retention_months=12.0))
    captured = {}
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: captured.update(kw))
    cell = cell_for(13.08, 80.27)
    centroid_lat, centroid_lon = cell_centroid(cell)
    old_report = SimpleNamespace(
        id="r1", h3_cell=cell, lat=13.08123, lon=80.27987, geom=None,
        created_at=datetime.now(timezone.utc) - timedelta(days=400),
        location_anonymized_at=None,
    )
    db = _Db()
    db.queue_scalars([old_report])

    count = service.anonymize_expired_reports(db)

    assert count == 1
    assert old_report.lat == pytest.approx(centroid_lat)
    assert old_report.lon == pytest.approx(centroid_lon)
    assert old_report.geom == f"SRID=4326;POINT({centroid_lon} {centroid_lat})"
    assert old_report.location_anonymized_at is not None
    assert db.committed
    assert captured["event_type"] == "opendata.locations_anonymized"
    assert captured["payload"]["count"] == 1


def test_anonymize_expired_reports_no_op_when_nothing_is_old_enough(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(open_data_retention_months=12.0))
    db = _Db()
    db.queue_scalars([])

    assert service.anonymize_expired_reports(db) == 0
