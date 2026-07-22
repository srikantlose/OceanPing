from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.modules.recovery import cv, service

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Db:
    """Fake DB double — scalars() queued in call order (test_narratives_
    service.py's style), plus get()/delete() for the missing-person paths
    (test_iot_service.py's style)."""

    def __init__(self, store=None):
        self._scalars_queue: list[list] = []
        self._store = dict(store or {})
        self.added: list = []
        self.deleted: list = []
        self.committed = False

    def queue_scalars(self, rows):
        self._scalars_queue.append(rows)

    def scalars(self, stmt):
        return _Rows(self._scalars_queue.pop(0))

    def get(self, model, pk):
        return self._store.get(pk)

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "id", None) is not None:
            self._store[obj.id] = obj

    def delete(self, obj):
        self.deleted.append(obj)
        self._store.pop(getattr(obj, "id", None), None)

    def flush(self):
        pass

    def commit(self):
        self.committed = True


def _patch_common(monkeypatch):
    monkeypatch.setattr(service, "get_or_create_reporter",
                        lambda db, source, external_id: SimpleNamespace(id="reporter-1"))
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: None)


def _settings(**overrides):
    base = dict(
        recovery_mutual_aid_max_km=5.0,
        recovery_missing_match_threshold=0.72,
        recovery_missing_match_max_km=25.0,
        recovery_missing_person_retention_days=180.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# --- damage assessment ---------------------------------------------------------


def test_submit_damage_assessment_persists_cv_result(monkeypatch, tmp_path):
    _patch_common(monkeypatch)
    photo_path = tmp_path / "photo.jpg"
    monkeypatch.setattr(service.media_mod, "save_upload", lambda data, filename: photo_path)
    monkeypatch.setattr(service.media_mod, "compute_phash", lambda path: "deadbeef")
    monkeypatch.setattr(
        service.cv, "classify_damage",
        lambda path: cv.DamageResult(damage_class="flooding", severity="severe", confidence=0.5,
                                      mode="heuristic", detail={"water_fraction": 0.6}),
    )
    db = _Db()

    assessment = service.submit_damage_assessment(
        db, source="web", external_id="c1", lat=13.0, lon=80.2,
        media_bytes=b"fake-bytes", media_filename="a.jpg",
    )

    assert assessment.damage_class == "flooding"
    assert assessment.severity == "severe"
    assert assessment.cv_mode == "heuristic"
    assert assessment.phash == "deadbeef"
    assert assessment.h3_cell
    assert db.committed


def test_submit_damage_assessment_audits_without_pii(monkeypatch, tmp_path):
    _patch_common(monkeypatch)
    captured = {}
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: captured.update(kw))
    monkeypatch.setattr(service.media_mod, "save_upload", lambda data, filename: tmp_path / "p.jpg")
    monkeypatch.setattr(service.media_mod, "compute_phash", lambda path: None)
    monkeypatch.setattr(
        service.cv, "classify_damage",
        lambda path: cv.DamageResult(damage_class="minor_or_none", severity="minor", confidence=0.3,
                                      mode="heuristic", detail={}),
    )

    service.submit_damage_assessment(
        _Db(), source="web", external_id="c1", lat=13.0, lon=80.2,
        media_bytes=b"x", media_filename="a.jpg",
    )

    assert captured["event_type"] == "recovery.damage_assessed"
    assert captured["payload"]["damage_class"] == "minor_or_none"


# --- mutual-aid board -----------------------------------------------------------


def test_create_relief_request_rejects_unknown_category(monkeypatch):
    _patch_common(monkeypatch)
    with pytest.raises(ValueError):
        service.create_relief_request(
            _Db(), source="web", external_id="c1", lat=13.0, lon=80.2, category="fuel"
        )


def test_create_relief_request_persists(monkeypatch):
    _patch_common(monkeypatch)
    db = _Db()

    req = service.create_relief_request(
        db, source="web", external_id="c1", lat=13.0, lon=80.2,
        category="water", people_count=4,
    )

    assert req.status == "open"
    assert req.people_count == 4
    assert db.committed


def test_create_aid_offer_rejects_unknown_category(monkeypatch):
    _patch_common(monkeypatch)
    with pytest.raises(ValueError):
        service.create_aid_offer(_Db(), source="web", external_id="c1", lat=13.0, lon=80.2, category="fuel")


def test_fulfill_relief_request_sets_status_and_audits(monkeypatch):
    _patch_common(monkeypatch)
    req = SimpleNamespace(id="req-1", status="open", fulfilled_by=None, fulfilled_at=None)

    service.fulfill_relief_request(_Db(), req, analyst="alice")

    assert req.status == "fulfilled"
    assert req.fulfilled_by == "alice"
    assert req.fulfilled_at is not None


def test_close_aid_offer_sets_status(monkeypatch):
    _patch_common(monkeypatch)
    offer = SimpleNamespace(id="offer-1", status="open")

    service.close_aid_offer(_Db(), offer, analyst="alice")

    assert offer.status == "closed"


def test_suggested_aid_matches_uses_engine_over_open_rows(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: _settings(recovery_mutual_aid_max_km=5.0))
    db = _Db()
    db.queue_scalars([SimpleNamespace(id="r1", category="water", lat=13.0, lon=80.2)])
    db.queue_scalars([SimpleNamespace(id="o1", category="water", lat=13.005, lon=80.2)])

    matches = service.suggested_aid_matches(db)

    assert len(matches) == 1
    assert matches[0]["request_id"] == "r1"
    assert matches[0]["offer_id"] == "o1"
    assert matches[0]["category"] == "water"
    assert 0 < matches[0]["distance_km"] < 5.0


# --- missing/found-person registry ----------------------------------------------


def test_create_missing_person_rejects_unknown_report_type(monkeypatch):
    _patch_common(monkeypatch)
    with pytest.raises(ValueError):
        service.create_missing_person(
            _Db(), source="web", external_id="c1", report_type="lost", name="Kavya Raman"
        )


def test_create_missing_person_without_location_has_no_h3_cell(monkeypatch):
    _patch_common(monkeypatch)
    db = _Db()

    person = service.create_missing_person(
        db, source="web", external_id="c1", report_type="missing", name="Kavya Raman"
    )

    assert person.h3_cell is None
    assert person.status == "open"


def test_create_missing_person_audit_omits_name(monkeypatch):
    """The audit chain shouldn't become a second, longer-lived place a
    missing person's name lives — see models.py::MissingPerson."""
    _patch_common(monkeypatch)
    captured = {}
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: captured.update(kw))

    service.create_missing_person(
        _Db(), source="web", external_id="c1", report_type="found",
        name="Kavya Raman", lat=13.0, lon=80.2,
    )

    assert "Kavya" not in str(captured["payload"])
    assert captured["payload"]["report_type"] == "found"


def test_candidate_matches_for_ranks_against_opposite_report_type(monkeypatch):
    monkeypatch.setattr(service, "get_settings",
                        lambda: _settings(recovery_missing_match_threshold=0.72, recovery_missing_match_max_km=25.0))
    person = SimpleNamespace(id="m1", report_type="missing", name="Kavya Raman", lat=None, lon=None)
    db = _Db()
    db.queue_scalars([SimpleNamespace(id="f1", report_type="found", name="Kavya Raman", lat=None, lon=None)])

    matches = service.candidate_matches_for(db, person)

    assert len(matches) == 1
    assert matches[0]["candidate_id"] == "f1"
    assert matches[0]["candidate_name"] == "Kavya Raman"


def test_resolve_missing_person_without_a_match(monkeypatch):
    _patch_common(monkeypatch)
    person = SimpleNamespace(id="m1", status="open", report_type="missing", matched_person_id=None,
                             resolved_by=None, resolved_at=None)

    service.resolve_missing_person(_Db(), person, analyst="alice")

    assert person.status == "resolved"
    assert person.resolved_by == "alice"


def test_resolve_missing_person_cross_links_a_match(monkeypatch):
    _patch_common(monkeypatch)
    missing = SimpleNamespace(id="m1", status="open", report_type="missing", matched_person_id=None,
                              resolved_by=None, resolved_at=None)
    found = SimpleNamespace(id="f1", status="open", report_type="found", matched_person_id=None,
                            resolved_by=None, resolved_at=None)
    db = _Db(store={"f1": found})

    service.resolve_missing_person(db, missing, analyst="alice", matched_person_id="f1")

    assert missing.status == "resolved" and found.status == "resolved"
    assert missing.matched_person_id == "f1" and found.matched_person_id == "m1"
    assert found.resolved_by == "alice"


def test_resolve_missing_person_rejects_matching_the_same_report_type(monkeypatch):
    _patch_common(monkeypatch)
    missing1 = SimpleNamespace(id="m1", status="open", report_type="missing", matched_person_id=None,
                               resolved_by=None, resolved_at=None)
    missing2 = SimpleNamespace(id="m2", status="open", report_type="missing", matched_person_id=None,
                               resolved_by=None, resolved_at=None)
    db = _Db(store={"m2": missing2})

    with pytest.raises(ValueError):
        service.resolve_missing_person(db, missing1, analyst="alice", matched_person_id="m2")


def test_resolve_missing_person_rejects_an_already_resolved_entry(monkeypatch):
    _patch_common(monkeypatch)
    person = SimpleNamespace(id="m1", status="resolved", report_type="missing", matched_person_id=None,
                             resolved_by="bob", resolved_at=NOW)

    with pytest.raises(ValueError):
        service.resolve_missing_person(_Db(), person, analyst="alice")


# --- retention purge -------------------------------------------------------------


def test_purge_expired_missing_persons_deletes_old_rows_and_removes_photo(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "get_settings", lambda: _settings(recovery_missing_person_retention_days=180.0))
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: None)
    photo = tmp_path / "person.jpg"
    photo.write_bytes(b"x")
    old_person = SimpleNamespace(id="old", created_at=NOW - timedelta(days=200), photo_path=str(photo),
                                 matched_person_id=None)
    db = _Db()
    db.queue_scalars([old_person])  # the expired-rows query
    db.queue_scalars([])            # the referencing-rows query

    purged = service.purge_expired_missing_persons(db)

    assert purged == 1
    assert old_person in db.deleted
    assert not photo.exists()
    assert db.committed


def test_purge_expired_missing_persons_unlinks_references_before_deleting(monkeypatch):
    """A still-fresh row pointing at an expired one via matched_person_id
    must not be left with a dangling FK — see service.py's comment on why
    this can't just rely on delete ordering."""
    monkeypatch.setattr(service, "get_settings", lambda: _settings(recovery_missing_person_retention_days=180.0))
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: None)
    old_person = SimpleNamespace(id="old", created_at=NOW - timedelta(days=200), photo_path=None,
                                 matched_person_id=None)
    fresh_referencer = SimpleNamespace(id="fresh", created_at=NOW, photo_path=None, matched_person_id="old")
    db = _Db()
    db.queue_scalars([old_person])          # expired-rows query
    db.queue_scalars([fresh_referencer])    # referencing-rows query

    service.purge_expired_missing_persons(db)

    assert fresh_referencer.matched_person_id is None
    assert old_person in db.deleted
    assert fresh_referencer not in db.deleted


def test_purge_expired_missing_persons_no_op_when_nothing_expired(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: _settings())
    db = _Db()
    db.queue_scalars([])  # expired-rows query; no referencing query since expired_ids is empty

    assert service.purge_expired_missing_persons(db) == 0
