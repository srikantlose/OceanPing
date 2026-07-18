import numpy as np

from app.modules.chat import service
from app.modules.nlp import classifier


class _FakeAlert:
    def __init__(self, id, tier, hazard_type, h3_cells, message=None, status="active"):
        self.id = id
        self.tier = tier
        self.hazard_type = hazard_type
        self.h3_cells = h3_cells
        self.message = message or {"en": f"{tier} for {hazard_type}"}
        self.status = status


class _FakeDoc:
    def __init__(self, id, title, content, embedding):
        self.id = id
        self.title = title
        self.content = content
        self.embedding = embedding


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.added = []
        self.committed = False

    def scalars(self, stmt):
        return _FakeScalars(self._rows)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True


class _FakeEmbedder:
    def __init__(self, vec):
        self._vec = vec

    def encode(self, texts, normalize_embeddings=True):
        return np.array([self._vec])


# --- is_evacuation_directive ---------------------------------------------

def test_is_evacuation_directive_true_cases():
    assert service.is_evacuation_directive("Should I evacuate right now?")
    assert service.is_evacuation_directive("do I need to evacuate my house")
    assert service.is_evacuation_directive("Am I in danger here")


def test_is_evacuation_directive_false_for_general_questions():
    assert not service.is_evacuation_directive("What does watch tier mean?")
    assert not service.is_evacuation_directive("How do I report an oil spill?")


# --- retrieve --------------------------------------------------------------

def test_retrieve_ranks_by_cosine_similarity():
    query = [1.0, 0.0]
    close = _FakeDoc("d1", "Close", "close content", [0.9, 0.1])
    far = _FakeDoc("d2", "Far", "far content", [0.0, 1.0])
    db = _FakeDb([far, close])
    hits = service.retrieve(db, query)
    assert [doc.id for doc, _ in hits] == ["d1", "d2"]
    assert hits[0][1] > hits[1][1]


def test_retrieve_skips_docs_without_embedding():
    query = [1.0, 0.0]
    has_embedding = _FakeDoc("d1", "Has", "content", [1.0, 0.0])
    no_embedding = _FakeDoc("d2", "None", "content", None)
    db = _FakeDb([has_embedding, no_embedding])
    hits = service.retrieve(db, query)
    assert [doc.id for doc, _ in hits] == ["d1"]


# --- answer(): evacuation-directive bypass ---------------------------------

def test_answer_evacuation_directive_never_reaches_embedder_or_llm(monkeypatch):
    calls = []
    monkeypatch.setattr(classifier, "_load_model", lambda: calls.append("embedder") or None)
    monkeypatch.setattr(service.llm_mod, "get_adapter", lambda: (_ for _ in ()).throw(AssertionError("LLM should not be called")))

    db = _FakeDb([])
    result = service.answer(db, "Should I evacuate now?", channel="web")

    assert calls == []  # embedder never even loaded - the bypass is unconditional
    assert result["fallback"] is True
    assert result["answer"] == service.get_settings().chat_helpline_message
    assert db.committed
    assert db.added[0].is_evacuation_directive is True


def test_answer_evacuation_directive_looks_up_alerts_by_cells():
    db = _FakeDb([_FakeAlert("a1", "watch", "coastal_flooding", ["cell-1", "cell-2"])])
    result = service.answer(db, "should I evacuate", channel="telegram", alert_cells=["cell-2", "cell-9"])
    assert len(result["alerts"]) == 1
    assert result["alerts"][0]["id"] == "a1"


def test_answer_evacuation_directive_with_no_location_returns_no_alerts():
    db = _FakeDb([_FakeAlert("a1", "watch", "coastal_flooding", ["cell-1"])])
    result = service.answer(db, "am I in danger", channel="web")
    assert result["alerts"] == []


# --- answer(): retrieval-threshold fallback (never reaches the LLM) --------

def test_answer_falls_back_when_no_embedder_available(monkeypatch):
    monkeypatch.setattr(classifier, "_load_model", lambda: None)
    db = _FakeDb([])
    result = service.answer(db, "What does watch tier mean?")
    assert result["fallback"] is True
    assert result["answer"] == service.get_settings().chat_helpline_message


def test_answer_falls_back_below_retrieval_threshold_without_calling_llm(monkeypatch):
    monkeypatch.setattr(classifier, "_load_model", lambda: _FakeEmbedder([1.0, 0.0]))
    monkeypatch.setattr(service.llm_mod, "get_adapter", lambda: (_ for _ in ()).throw(AssertionError("LLM should not be called")))
    settings = service.get_settings()
    monkeypatch.setattr(settings, "chat_retrieval_threshold", 0.9)

    # orthogonal document -> cosine similarity 0.0, well below threshold
    db = _FakeDb([_FakeDoc("d1", "Unrelated", "unrelated content", [0.0, 1.0])])
    result = service.answer(db, "some question")
    assert result["fallback"] is True
    assert result["sources"] == []


# --- answer(): real LLM path -------------------------------------------------

class _FakeLLM:
    def __init__(self, text):
        self._text = text

    def complete(self, system, user_message):
        assert "Context:" in user_message
        return self._text


def test_answer_calls_llm_when_retrieval_is_strong(monkeypatch):
    monkeypatch.setattr(classifier, "_load_model", lambda: _FakeEmbedder([1.0, 0.0]))
    monkeypatch.setattr(service.llm_mod, "get_adapter", lambda: _FakeLLM("Here is the answer."))
    settings = service.get_settings()
    monkeypatch.setattr(settings, "chat_retrieval_threshold", 0.5)

    db = _FakeDb([_FakeDoc("d1", "Relevant", "relevant content", [1.0, 0.0])])
    result = service.answer(db, "some question")

    assert result["fallback"] is False
    assert result["answer"] == "Here is the answer."
    assert result["sources"] == [{"id": "d1", "title": "Relevant"}]
    assert db.added[0].retrieved_doc_ids == ["d1"]


def test_answer_falls_back_when_llm_returns_none(monkeypatch):
    monkeypatch.setattr(classifier, "_load_model", lambda: _FakeEmbedder([1.0, 0.0]))
    monkeypatch.setattr(service.llm_mod, "get_adapter", lambda: _FakeLLM(None))
    settings = service.get_settings()
    monkeypatch.setattr(settings, "chat_retrieval_threshold", 0.5)

    db = _FakeDb([_FakeDoc("d1", "Relevant", "relevant content", [1.0, 0.0])])
    result = service.answer(db, "some question")

    assert result["fallback"] is True
    assert result["answer"] == settings.chat_helpline_message
    assert result["sources"] == []
