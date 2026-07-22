"""The three bus-mode pipeline consumers (phase 3, milestone 8): each is just
a processing_stage gate plus a call to logic this project already tests
elsewhere (classifier.classify/embed, nlp/dedup.py::assign_incident,
scoring/service.py::rescore_report) — so these tests cover the NEW wiring
(stage gating, hazard_locked semantics, stage advancement, topic hand-off),
not the underlying pipeline functions themselves."""
from types import SimpleNamespace

from app.modules.ingest.consumers import dedup as dedup_consumer
from app.modules.ingest.consumers import nlp as nlp_consumer
from app.modules.ingest.consumers import scoring as scoring_consumer
from app.modules.nlp.classifier import Classification


class _FakeDb:
    def __init__(self, reports):
        self._reports = reports
        self.committed = 0

    def get(self, model, pk):
        return self._reports.get(pk)

    def commit(self):
        self.committed += 1


# --- nlp consumer ----------------------------------------------------------------


def _queued_report(**overrides):
    base = dict(
        id="r1", processing_stage="queued", hazard_locked=False,
        hazard_type="other", text="water rising fast near the shore",
        embedding=None, confidence_components={"media": 0.5},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_nlp_process_one_skips_reports_not_in_queued_stage(monkeypatch):
    report = _queued_report(processing_stage="classified")
    db = _FakeDb({"r1": report})
    called = []
    monkeypatch.setattr(
        nlp_consumer.classifier, "classify",
        lambda text: called.append(text) or Classification(hazard_type="tsunami"),
    )
    nlp_consumer.process_one(db, "r1")
    assert called == []
    assert db.committed == 0


def test_nlp_process_one_classifies_when_not_locked(monkeypatch):
    report = _queued_report()
    db = _FakeDb({"r1": report})
    monkeypatch.setattr(
        nlp_consumer.classifier, "classify",
        lambda text: Classification(hazard_type="tsunami", embedding=[0.1, 0.2], mode="embedding"),
    )
    produced = []
    monkeypatch.setattr(nlp_consumer.bus, "produce", lambda topic, rid: produced.append((topic, rid)))

    nlp_consumer.process_one(db, "r1")

    assert report.hazard_type == "tsunami"
    assert report.hazard_locked is True
    assert report.embedding == [0.1, 0.2]
    assert report.confidence_components["nlp_mode"] == "embedding"
    assert report.processing_stage == "classified"
    assert db.committed == 1
    assert produced == [(nlp_consumer.bus.TOPIC_CLASSIFIED, "r1")]


def test_nlp_process_one_never_overwrites_an_explicit_hazard_pick(monkeypatch):
    """create_report()'s longstanding rule — the reporter's own pick wins —
    still has to hold once classification happens out-of-band."""
    report = _queued_report(hazard_locked=True, hazard_type="tsunami")
    db = _FakeDb({"r1": report})
    monkeypatch.setattr(
        nlp_consumer.classifier, "classify",
        lambda text: Classification(hazard_type="oil_spill", embedding=[0.9], mode="embedding"),
    )
    monkeypatch.setattr(nlp_consumer.bus, "produce", lambda *a: None)

    nlp_consumer.process_one(db, "r1")

    assert report.hazard_type == "tsunami"  # untouched
    assert report.embedding == [0.9]  # still computed regardless of the lock


def test_nlp_process_one_falls_back_to_embed_when_classification_has_no_embedding(monkeypatch):
    report = _queued_report()
    db = _FakeDb({"r1": report})
    monkeypatch.setattr(
        nlp_consumer.classifier, "classify",
        lambda text: Classification(hazard_type="flood", embedding=None, mode="keyword"),
    )
    monkeypatch.setattr(nlp_consumer.classifier, "embed", lambda text: [0.5, 0.5])
    monkeypatch.setattr(nlp_consumer.bus, "produce", lambda *a: None)

    nlp_consumer.process_one(db, "r1")

    assert report.embedding == [0.5, 0.5]


def test_nlp_process_one_missing_report_is_a_noop():
    db = _FakeDb({})
    nlp_consumer.process_one(db, "does-not-exist")
    assert db.committed == 0


# --- dedup consumer ----------------------------------------------------------------


def _classified_report(**overrides):
    base = dict(
        id="r1", processing_stage="classified", hazard_type="flood",
        h3_cell="abc", lang="en", source="web", incident_id=None,
        confidence_components={"nlp_mode": "embedding"},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_dedup_process_one_skips_reports_not_in_classified_stage(monkeypatch):
    report = _classified_report(processing_stage="assigned")
    db = _FakeDb({"r1": report})
    called = []
    monkeypatch.setattr(dedup_consumer, "assign_incident", lambda db, r: called.append(r))
    dedup_consumer.process_one(db, "r1")
    assert called == []
    assert db.committed == 0


def test_dedup_process_one_assigns_audits_and_advances_stage(monkeypatch):
    report = _classified_report()
    db = _FakeDb({"r1": report})

    def fake_assign(db, r):
        r.incident_id = "inc-1"

    monkeypatch.setattr(dedup_consumer, "assign_incident", fake_assign)
    audited = []
    monkeypatch.setattr(
        dedup_consumer, "audit_report_created",
        lambda db, r, nlp_mode: audited.append((r.id, nlp_mode)),
    )
    produced = []
    monkeypatch.setattr(dedup_consumer.bus, "produce", lambda topic, rid: produced.append((topic, rid)))

    dedup_consumer.process_one(db, "r1")

    assert report.incident_id == "inc-1"
    assert audited == [("r1", "embedding")]
    assert report.processing_stage == "assigned"
    assert db.committed == 1
    assert produced == [(dedup_consumer.bus.TOPIC_ASSIGNED, "r1")]


def test_dedup_process_one_missing_report_is_a_noop():
    db = _FakeDb({})
    dedup_consumer.process_one(db, "does-not-exist")
    assert db.committed == 0


# --- scoring consumer ----------------------------------------------------------------


def _assigned_report(**overrides):
    base = dict(id="r1", processing_stage="assigned")
    base.update(overrides)
    return SimpleNamespace(**base)


def test_scoring_process_one_skips_reports_not_in_assigned_stage(monkeypatch):
    report = _assigned_report(processing_stage="scored")
    db = _FakeDb({"r1": report})
    called = []
    monkeypatch.setattr(scoring_consumer, "rescore_report", lambda db, r: called.append(r))
    scoring_consumer.process_one(db, "r1")
    assert called == []
    assert db.committed == 0


def test_scoring_process_one_rescores_and_advances_stage(monkeypatch):
    report = _assigned_report()
    db = _FakeDb({"r1": report})
    called = []
    monkeypatch.setattr(scoring_consumer, "rescore_report", lambda db, r: called.append(r))

    scoring_consumer.process_one(db, "r1")

    assert called == [report]
    assert report.processing_stage == "scored"
    assert db.committed == 1


def test_scoring_process_one_missing_report_is_a_noop():
    db = _FakeDb({})
    scoring_consumer.process_one(db, "does-not-exist")
    assert db.committed == 0
