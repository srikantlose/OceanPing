import numpy as np

from app.modules.chat import corpus
from app.modules.nlp import classifier


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
    ids = [entry["id"] for entry in corpus.HAZARD_FAQ]
    assert len(ids) == len(set(ids))


def test_hazard_faq_entries_have_title_and_content():
    for entry in corpus.HAZARD_FAQ:
        assert entry["title"].strip()
        assert entry["content"].strip()
        assert len(entry["content"]) > 20  # not a placeholder stub


def test_hazard_faq_never_contains_evacuation_directives():
    # The corpus is educational content only - it should never itself read as
    # a real-time evacuation directive, since chat/service.py's safety gate
    # depends on the LLM only ever seeing passages like these.
    from app.modules.chat.service import is_evacuation_directive

    for entry in corpus.HAZARD_FAQ:
        assert not is_evacuation_directive(entry["content"])


def test_seed_corpus_upserts_and_embeds(monkeypatch):
    monkeypatch.setattr(classifier, "_load_model", lambda: _FakeEmbedder())
    db = _FakeDb()
    updated = corpus.seed_corpus(db)
    assert updated == len(corpus.HAZARD_FAQ)
    assert db.committed
    assert len(db._rows) == len(corpus.HAZARD_FAQ)
    sample = db._rows["faq-hazard-types"]
    assert sample.title
    assert sample.content
    assert sample.embedding == [0.1, 0.2, 0.3]


def test_seed_corpus_skips_embedding_when_model_unavailable(monkeypatch):
    monkeypatch.setattr(classifier, "_load_model", lambda: None)
    db = _FakeDb()
    updated = corpus.seed_corpus(db)
    assert updated == len(corpus.HAZARD_FAQ)
    for doc in db._rows.values():
        assert doc.embedding is None
        assert doc.title  # still stored, just not retrievable by vector search yet
