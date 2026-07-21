from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.modules.narratives import service

T0 = datetime(2026, 7, 21, tzinfo=timezone.utc)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Db:
    """Fake DB double supporting execute()/scalars() as queues popped in call
    order, plus add()/flush()/commit() — same style as
    test_forecast_service.py's fake, generalized to cover both query styles
    this module uses."""

    def __init__(self):
        self._scalars_queue: list[list] = []
        self.added: list = []
        self.committed = False

    def queue_scalars(self, rows):
        self._scalars_queue.append(rows)

    def scalars(self, stmt):
        return _Rows(self._scalars_queue.pop(0))

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.committed = True


class _FakeLLM:
    """Same shape as test_chat_service.py's _FakeLLM — a text answer, or None
    to simulate "no adapter configured"."""

    def __init__(self, text):
        self._text = text

    def complete(self, system, user_message):
        return self._text


def _report_row(i, embedding, hazard_type="tsunami", lat=13.0, lon=80.2, status="unverified", text=None, h3_cell="cellA"):
    return SimpleNamespace(
        id=f"r{i}",
        h3_cell=h3_cell,
        embedding=embedding,
        text=text or f"text {i}",
        hazard_type=hazard_type,
        lat=lat,
        lon=lon,
        status=status,
        created_at=T0 + timedelta(minutes=i),
    )


def _detect_settings():
    return SimpleNamespace(narrative_window_hours=12.0, narrative_sim_threshold=0.9)


# --- _overlaps -------------------------------------------------------------------


def test_overlaps_true_when_majority_shared():
    assert service._overlaps(["a", "b"], ["b", "c"])


def test_overlaps_false_when_no_shared_ids():
    assert not service._overlaps(["a", "b"], ["c", "d"])


def test_overlaps_false_when_either_side_empty():
    assert not service._overlaps([], ["a"])
    assert not service._overlaps(["a"], [])


# --- detect_narratives -------------------------------------------------------------


def test_detect_narratives_flags_a_contradicting_cluster(monkeypatch):
    monkeypatch.setattr(service, "get_settings", _detect_settings)
    monkeypatch.setattr(service, "instrument_zscores_near", lambda db, hz, lat, lon: [])
    monkeypatch.setattr(service, "nearest_pilot_location", lambda lat, lon: "Marina Beach")
    monkeypatch.setattr(service.llm_mod, "get_adapter", lambda: _FakeLLM(None))
    captured = []
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: captured.append(kw))

    a = [1.0, 0.0, 0.0]
    rows = [_report_row(i, a, hazard_type="tsunami") for i in range(3)]
    db = _Db()
    db.queue_scalars(rows)  # _candidate_reports
    db.queue_scalars([])    # _matching_open_narrative -> nothing existing

    n = service.detect_narratives(db)

    assert n == 1
    assert len(db.added) == 1
    narrative = db.added[0]
    assert narrative.hazard_type == "tsunami"
    assert narrative.report_count == 3
    assert narrative.instrument_flat is True
    assert narrative.status == "draft"
    assert narrative.draft_method == "template"
    assert narrative.message["en"]["standard"]
    assert db.committed
    assert captured[0]["event_type"] == "narrative.flagged"


def test_detect_narratives_skips_cluster_that_does_not_contradict_anything(monkeypatch):
    monkeypatch.setattr(service, "get_settings", _detect_settings)
    monkeypatch.setattr(
        service, "instrument_zscores_near",
        lambda db, hz, lat, lon: [{"station_id": "s1", "variable": "water_level", "zscore": 4.0}],
    )
    a = [1.0, 0.0, 0.0]
    rows = [_report_row(i, a, hazard_type="tsunami") for i in range(3)]
    db = _Db()
    db.queue_scalars(rows)  # _candidate_reports only — never reaches _matching_open_narrative

    n = service.detect_narratives(db)

    assert n == 0
    assert db.added == []
    assert db.committed


def _existing_narrative(status):
    return SimpleNamespace(
        id="n1", hazard_type="tsunami", status=status,
        report_ids=["r0", "r1"], report_count=2, h3_cells=["cellA"],
        centroid_lat=13.0, centroid_lon=80.2, instrument_flat=True, rejected_report_count=0,
    )


def test_detect_narratives_updates_existing_draft_instead_of_duplicating(monkeypatch):
    monkeypatch.setattr(service, "get_settings", _detect_settings)
    monkeypatch.setattr(service, "instrument_zscores_near", lambda db, hz, lat, lon: [])
    captured = []
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: captured.append(kw))

    a = [1.0, 0.0, 0.0]
    rows = [_report_row(i, a, hazard_type="tsunami") for i in range(3)]
    existing = _existing_narrative("draft")
    db = _Db()
    db.queue_scalars(rows)         # _candidate_reports
    db.queue_scalars([existing])   # _matching_narrative -> overlaps

    n = service.detect_narratives(db)

    assert n == 0  # a refresh, not a new flag
    assert existing.report_count == 3
    assert existing.report_ids == ["r0", "r1", "r2"]
    assert db.added == []
    assert captured[0]["event_type"] == "narrative.updated"


def test_detect_narratives_does_not_reflag_a_dismissed_narrative(monkeypatch):
    """An analyst already judged this claim — re-raising it every tick would
    just spam the queue with a decision that's been made."""
    monkeypatch.setattr(service, "get_settings", _detect_settings)
    monkeypatch.setattr(service, "instrument_zscores_near", lambda db, hz, lat, lon: [])
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: None)

    a = [1.0, 0.0, 0.0]
    rows = [_report_row(i, a, hazard_type="tsunami") for i in range(3)]
    dismissed = _existing_narrative("dismissed")
    db = _Db()
    db.queue_scalars(rows)
    db.queue_scalars([dismissed])

    n = service.detect_narratives(db)

    assert n == 0
    assert db.added == []
    assert dismissed.report_count == 2  # left untouched, not refreshed


def test_detect_narratives_drafts_afresh_when_the_match_was_already_approved(monkeypatch):
    """An approved narrative's correction has already been sent — it's a
    finished artifact. A rumor resurging past it is new information and gets
    its own draft rather than silently mutating what went out."""
    monkeypatch.setattr(service, "get_settings", _detect_settings)
    monkeypatch.setattr(service, "instrument_zscores_near", lambda db, hz, lat, lon: [])
    monkeypatch.setattr(service, "nearest_pilot_location", lambda lat, lon: "Marina Beach")
    monkeypatch.setattr(service.llm_mod, "get_adapter", lambda: _FakeLLM(None))
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: None)

    a = [1.0, 0.0, 0.0]
    rows = [_report_row(i, a, hazard_type="tsunami") for i in range(3)]
    approved = _existing_narrative("approved")
    db = _Db()
    db.queue_scalars(rows)
    db.queue_scalars([approved])

    n = service.detect_narratives(db)

    assert n == 1
    assert len(db.added) == 1
    assert db.added[0].status == "draft"
    assert approved.report_count == 2  # the sent narrative is never rewritten


def test_detect_narratives_uses_llm_polish_for_english_only(monkeypatch):
    monkeypatch.setattr(service, "get_settings", _detect_settings)
    monkeypatch.setattr(service, "instrument_zscores_near", lambda db, hz, lat, lon: [])
    monkeypatch.setattr(service, "nearest_pilot_location", lambda lat, lon: "Marina Beach")
    monkeypatch.setattr(service.llm_mod, "get_adapter", lambda: _FakeLLM("Polished correction text."))
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: None)

    a = [1.0, 0.0, 0.0]
    rows = [_report_row(i, a, hazard_type="tsunami") for i in range(3)]
    db = _Db()
    db.queue_scalars(rows)
    db.queue_scalars([])

    service.detect_narratives(db)

    narrative = db.added[0]
    assert narrative.draft_method == "llm"
    assert narrative.message["en"]["standard"] == "Polished correction text."
    assert narrative.message["ta"]["standard"] != "Polished correction text."
    assert narrative.message["te"]["standard"] != "Polished correction text."


def test_detect_narratives_rejected_reports_flag_even_without_instrument_signal(monkeypatch):
    """oil_spill has no instrument signal at all — only the rejected-report
    path can flag it."""
    monkeypatch.setattr(service, "get_settings", _detect_settings)
    monkeypatch.setattr(service, "instrument_zscores_near", lambda db, hz, lat, lon: [])
    monkeypatch.setattr(service, "nearest_pilot_location", lambda lat, lon: "Marina Beach")
    monkeypatch.setattr(service.llm_mod, "get_adapter", lambda: _FakeLLM(None))
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: None)

    a = [1.0, 0.0, 0.0]
    rows = [
        _report_row(0, a, hazard_type="oil_spill", status="rejected"),
        _report_row(1, a, hazard_type="oil_spill", status="unverified"),
        _report_row(2, a, hazard_type="oil_spill", status="unverified"),
    ]
    db = _Db()
    db.queue_scalars(rows)
    db.queue_scalars([])

    n = service.detect_narratives(db)

    assert n == 1
    assert db.added[0].rejected_report_count == 1
    assert db.added[0].instrument_flat is False  # oil_spill has no instrument signal to check


# --- approve_narrative / dismiss_narrative -----------------------------------------


def test_approve_narrative_rejects_non_draft_status():
    narrative = SimpleNamespace(status="approved")
    with pytest.raises(ValueError):
        service.approve_narrative(_Db(), narrative, analyst="alice")


def test_dismiss_narrative_rejects_non_draft_status():
    narrative = SimpleNamespace(status="dismissed")
    with pytest.raises(ValueError):
        service.dismiss_narrative(_Db(), narrative, analyst="alice")


def test_dismiss_narrative_sets_status_and_audits(monkeypatch):
    captured = {}
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: captured.update(kw))
    narrative = SimpleNamespace(status="draft", id="n1")
    db = _Db()

    out = service.dismiss_narrative(db, narrative, analyst="alice")

    assert out.status == "dismissed"
    assert out.reviewed_by == "alice"
    assert captured["event_type"] == "narrative.dismissed"
    assert db.committed


def test_approve_narrative_marks_approved_and_delivers(monkeypatch):
    monkeypatch.setattr(service, "append_audit", lambda db, **kw: None)
    delivered = {}

    def _fake_deliver(db, n):
        delivered["narrative"] = n
        return 3

    monkeypatch.setattr(service, "deliver_narrative_correction", _fake_deliver)
    narrative = SimpleNamespace(status="draft", id="n1")
    db = _Db()

    n_delivered = service.approve_narrative(db, narrative, analyst="alice")

    assert narrative.status == "approved"
    assert narrative.reviewed_by == "alice"
    assert n_delivered == 3
    assert delivered["narrative"] is narrative
    assert db.committed


# --- _matching_subscriptions / deliver_narrative_correction ------------------------


def test_matching_subscriptions_filters_by_h3_overlap():
    narrative = SimpleNamespace(h3_cells=["cellA", "cellB"])
    sub_in = SimpleNamespace(h3_cells=["cellB", "cellZ"])
    sub_out = SimpleNamespace(h3_cells=["cellX"])
    db = _Db()
    db.queue_scalars([sub_in, sub_out])
    assert service._matching_subscriptions(db, narrative) == [sub_in]


def test_matching_subscriptions_empty_when_narrative_has_no_cells():
    narrative = SimpleNamespace(h3_cells=[])
    assert service._matching_subscriptions(_Db(), narrative) == []


def test_deliver_narrative_correction_sends_via_channel_adapter_and_logs(monkeypatch):
    narrative = SimpleNamespace(
        id="n1", h3_cells=["cellA"], message={"en": {"standard": "x", "short": "y"}}, hazard_type="tsunami",
    )
    sub = SimpleNamespace(id="sub1", channel="telegram", h3_cells=["cellA"], lang="en")

    class _FakeAdapter:
        def send(self, alert, subscription):
            assert alert.tier == "correction"
            assert alert.hazard_type == "tsunami"
            assert alert.message == narrative.message
            return SimpleNamespace(status="sent", detail=None)

    monkeypatch.setattr(service, "get_adapter", lambda channel: _FakeAdapter())
    monkeypatch.setattr(service, "_matching_subscriptions", lambda db, n: [sub])
    db = _Db()

    n_sent = service.deliver_narrative_correction(db, narrative)

    assert n_sent == 1
    assert len(db.added) == 1
    assert db.added[0].status == "sent"
    assert db.committed


def test_deliver_narrative_correction_skips_channel_with_no_adapter(monkeypatch):
    narrative = SimpleNamespace(id="n1", h3_cells=["cellA"], message={}, hazard_type="tsunami")
    sub = SimpleNamespace(id="sub1", channel="carrier_pigeon", h3_cells=["cellA"], lang="en")
    monkeypatch.setattr(service, "get_adapter", lambda channel: None)
    monkeypatch.setattr(service, "_matching_subscriptions", lambda db, n: [sub])
    db = _Db()

    n_sent = service.deliver_narrative_correction(db, narrative)

    assert n_sent == 0
    assert db.added == []


def test_deliver_narrative_correction_records_failure_without_raising(monkeypatch):
    narrative = SimpleNamespace(id="n1", h3_cells=["cellA"], message={}, hazard_type="tsunami")
    sub = SimpleNamespace(id="sub1", channel="telegram", h3_cells=["cellA"], lang="en")

    class _FailingAdapter:
        def send(self, alert, subscription):
            raise RuntimeError("boom")

    monkeypatch.setattr(service, "get_adapter", lambda channel: _FailingAdapter())
    monkeypatch.setattr(service, "_matching_subscriptions", lambda db, n: [sub])
    db = _Db()

    n_sent = service.deliver_narrative_correction(db, narrative)

    assert n_sent == 1
    assert db.added[0].status == "failed"
