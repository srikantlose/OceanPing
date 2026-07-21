from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.modules.safety import service

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Db:
    """Fake DB double — same style as test_narratives_service.py's."""

    def __init__(self, scalars_rows=None, scalar_result=None):
        self._scalars_rows = scalars_rows or []
        self._scalar_result = scalar_result
        self.added: list = []
        self.committed = False

    def scalars(self, stmt):
        return _Rows(self._scalars_rows)

    def scalar(self, stmt):
        return self._scalar_result

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.committed = True


def _checkin(status="safe", observed_at=None):
    return SimpleNamespace(status=status, observed_at=observed_at or NOW)


def _patch_deps(monkeypatch):
    monkeypatch.setattr(service, "get_or_create_reporter",
                        lambda db, source, external_id: SimpleNamespace(id="reporter-1"))
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: None)


# --- create_checkin ---------------------------------------------------------


def test_create_checkin_rejects_an_unknown_status():
    with pytest.raises(ValueError):
        service.create_checkin(
            _Db(), source="mobile", external_id="c1", status="probably_fine", lat=13.0, lon=80.2
        )


def test_create_checkin_persists_and_commits(monkeypatch):
    _patch_deps(monkeypatch)
    db = _Db()

    checkin = service.create_checkin(
        db, source="mobile", external_id="c1", status="need_help",
        lat=13.05, lon=80.28, note="stuck on the roof",
    )

    assert checkin.status == "need_help"
    assert checkin.note == "stuck on the roof"
    assert checkin.h3_cell
    assert db.added == [checkin]
    assert db.committed


def test_create_checkin_audits_the_event(monkeypatch):
    monkeypatch.setattr(service, "get_or_create_reporter",
                        lambda db, source, external_id: SimpleNamespace(id="reporter-1"))
    captured = {}
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: captured.update(kw))

    service.create_checkin(_Db(), source="mobile", external_id="c1", status="safe", lat=13.0, lon=80.2)

    assert captured["event_type"] == "safety.checkin"
    assert captured["subject_type"] == "safety_checkin"
    assert captured["payload"]["status"] == "safe"


def test_create_checkin_keeps_a_queued_observation_time(monkeypatch):
    _patch_deps(monkeypatch)
    observed = NOW - timedelta(hours=4)

    checkin = service.create_checkin(
        _Db(), source="mobile", external_id="c1", status="safe",
        lat=13.0, lon=80.2, observed_at=observed,
    )

    assert checkin.observed_at == observed


def test_create_checkin_never_writes_a_report(monkeypatch):
    """A check-in is a statement about a person, not a hazard observation —
    it must never reach the scoring/incident path."""
    _patch_deps(monkeypatch)
    db = _Db()

    service.create_checkin(db, source="mobile", external_id="c1", status="safe", lat=13.0, lon=80.2)

    assert len(db.added) == 1
    assert type(db.added[0]).__name__ == "SafetyCheckin"


# --- find_by_client_key -----------------------------------------------------


def test_find_by_client_key_returns_none_without_a_key():
    assert service.find_by_client_key(_Db(scalar_result=object()), None) is None
    assert service.find_by_client_key(_Db(scalar_result=object()), "") is None


def test_find_by_client_key_returns_the_existing_checkin():
    existing = _checkin()
    assert service.find_by_client_key(_Db(scalar_result=existing), "key-1") is existing


# --- counts -----------------------------------------------------------------


def test_checkin_counts_splits_by_status():
    rows = [_checkin("safe"), _checkin("safe"), _checkin("need_help")]
    counts = service.checkin_counts(_Db(scalars_rows=rows))
    assert counts["total"] == 3
    assert counts["safe"] == 2
    assert counts["need_help"] == 1


def test_checkin_counts_on_an_empty_window():
    counts = service.checkin_counts(_Db(scalars_rows=[]))
    assert counts == {"window_hours": 48.0, "total": 0, "safe": 0, "need_help": 0}
