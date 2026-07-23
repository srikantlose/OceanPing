import numpy as np

from app.modules.chat import corpus
from app.modules.hazards.registry import faq_entries
from app.modules.nlp import classifier


def _all_faq():
    return corpus.GENERAL_FAQ + faq_entries()


class _FakeEmbedder:
    def encode(self, texts, normalize_embeddings=True):
        return np.array([[0.1, 0.2, 0.3]])


class _FakeDoc:
    def __init__(self, id):
        self.id = id
        self.title = None
        self.content = None
        self.lang = None
        self.source = None
        self.embedding = None


class _FakeDb:
    def __init__(self):
        self._rows: dict[str, _FakeDoc] = {}
        self.committed = False

    def get(self, model, doc_id):
        return self._rows.get(doc_id)

    def add(self, doc):
        self._rows[doc.id] = doc

    def commit(self):
        self.committed = True


def test_hazard_faq_ids_are_unique():
    ids = [entry["id"] for entry in _all_faq()]
    assert len(ids) == len(set(ids))


def test_hazard_faq_entries_have_title_and_content():
    for entry in _all_faq():
        assert entry["title"].strip()
        assert entry["content"].strip()
        assert len(entry["content"]) > 20  # not a placeholder stub


def test_hazard_faq_never_contains_evacuation_directives():
    # The corpus is educational content only - it should never itself read as
    # a real-time evacuation directive, since chat/service.py's safety gate
    # depends on the LLM only ever seeing passages like these.
    from app.modules.chat.service import is_evacuation_directive

    for entry in _all_faq():
        assert not is_evacuation_directive(entry["content"])


def test_faq_hazard_types_entry_stays_in_sync_with_the_registry():
    # Moving per-hazard FAQ entries into the hazard registry (phase 4,
    # milestone 2) means this overview entry's hazard count/list must be
    # generated from the registry, not hand-copied, or it silently goes
    # stale the next time a hazard is added.
    from app.modules.hazards.registry import HAZARD_TYPES

    entry = next(e for e in corpus.GENERAL_FAQ if e["id"] == "faq-hazard-types")
    assert str(len(HAZARD_TYPES) - 1) in entry["content"]
    assert "tsunami" in entry["content"]
    assert "other" not in entry["content"].split(":")[1]  # the catch-all bucket isn't listed by name


def test_per_hazard_faq_entries_come_from_the_registry():
    entries = faq_entries()
    ids = {e["id"] for e in entries}
    assert "faq-tsunami-signs" in ids
    assert "faq-oil-spill-report" in ids
    # storm_surge and high_waves have no FAQ entry, same as before this refactor
    assert not any("storm" in e["id"] for e in entries)


def test_seed_corpus_upserts_and_embeds(monkeypatch):
    monkeypatch.setattr(classifier, "_load_model", lambda: _FakeEmbedder())
    db = _FakeDb()
    updated = corpus.seed_corpus(db)
    assert updated == len(_all_faq())
    assert db.committed
    assert len(db._rows) == len(_all_faq())
    sample = db._rows["faq-hazard-types"]
    assert sample.title
    assert sample.content
    assert sample.embedding == [0.1, 0.2, 0.3]


def test_seed_corpus_skips_embedding_when_model_unavailable(monkeypatch):
    monkeypatch.setattr(classifier, "_load_model", lambda: None)
    db = _FakeDb()
    updated = corpus.seed_corpus(db)
    assert updated == len(_all_faq())
    for doc in db._rows.values():
        assert doc.embedding is None
        assert doc.title  # still stored, just not retrievable by vector search yet
