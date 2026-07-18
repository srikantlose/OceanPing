"""RAG chatbot orchestration (phase 2, milestone 3): retrieval-augmented
answers grounded in rag_documents (see corpus.py), with two hard, code-level
safety rules the plan calls for - enforced here, not just in the LLM prompt:

  1. Evacuation-directive questions ("should I evacuate?") never reach the
     LLM at all - they get a canned helpline message plus a live lookup of
     active alerts near the asker's location, if given.
  2. Any other question whose best retrieval match is below
     chat_retrieval_threshold never reaches the LLM either - same fallback.
     The LLM only ever sees context passages we already decided were
     relevant enough to answer from.

Every question is logged to chat_logs, including which path it took, so the
answers are reviewable regardless of whether an Anthropic API key is even
configured.
"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Alert, ChatLog, RagDocument
from app.modules.alerts.geofence import cells_around
from app.modules.chat import llm as llm_mod
from app.modules.nlp import classifier as nlp_classifier
from app.modules.nlp.dedup import cosine

log = logging.getLogger(__name__)

RETRIEVAL_TOP_K = 4

# Keyword heuristic, not a trained classifier - no labeled data exists for
# this either, same honest scoping as classifier.py::detect_hearsay().
EVACUATION_MARKERS = [
    "should i evacuate", "should we evacuate", "do i need to evacuate", "need to evacuate",
    "is it safe to stay", "should i leave now", "should i leave", "am i in danger",
    "is my area safe", "will it reach my house", "should i move to higher ground",
]

SYSTEM_PROMPT = (
    "You are OceanPing's coastal-hazard information assistant. Answer only using the "
    "context passages provided below - if they don't contain the answer, say you don't "
    "know and direct the user to check official alerts or a disaster helpline. Never "
    "give a real-time evacuation directive (e.g. telling someone to evacuate, or that "
    "they are safe right now) - always defer real-time safety decisions to official "
    "alerts and local authorities. Respond in the same language as the question. Keep "
    "answers brief and factual."
)


def is_evacuation_directive(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in EVACUATION_MARKERS)


def _active_alerts_in_cells(db: Session, cells: set[str]) -> list[dict]:
    alerts = db.scalars(select(Alert).where(Alert.status == "active")).all()
    return [
        {"id": str(a.id), "tier": a.tier, "hazard_type": a.hazard_type, "message": a.message.get("en", "")}
        for a in alerts
        if cells & set(a.h3_cells or [])
    ]


def _active_alerts_near(db: Session, lat: float, lon: float) -> list[dict]:
    settings = get_settings()
    cells = set(cells_around(lat, lon, settings.subscription_radius_rings))
    return _active_alerts_in_cells(db, cells)


def retrieve(db: Session, query_vec: list[float]) -> list[tuple[RagDocument, float]]:
    """Small, code-seeded corpus - fetch then cosine-rank in Python, the same
    pattern nlp/dedup.py already uses for incident-merge similarity (no
    pgvector SQL distance operator is used anywhere in this codebase yet)."""
    docs = db.scalars(select(RagDocument)).all()
    scored = [(doc, cosine(query_vec, doc.embedding)) for doc in docs if doc.embedding is not None]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:RETRIEVAL_TOP_K]


def _log(
    db: Session, *, channel: str, question: str, answer_text: str, doc_ids: list[str],
    retrieval_score: float | None, is_evac: bool, is_fallback: bool,
) -> None:
    db.add(
        ChatLog(
            channel=channel,
            question=question,
            answer=answer_text,
            retrieved_doc_ids=doc_ids,
            retrieval_score=retrieval_score,
            is_evacuation_directive=is_evac,
            is_fallback=is_fallback,
        )
    )
    db.commit()


def answer(
    db: Session, question: str, *, channel: str = "web",
    lat: float | None = None, lon: float | None = None,
    alert_cells: list[str] | None = None,
) -> dict:
    """lat/lon is the web client's browser-geolocation shape; alert_cells lets
    the Telegram bot pass a subscriber's already-stored geofence directly
    instead of a lossy cells->lat/lon->cells round trip."""
    settings = get_settings()

    if is_evacuation_directive(question):
        if alert_cells is not None:
            alerts = _active_alerts_in_cells(db, set(alert_cells))
        elif lat is not None and lon is not None:
            alerts = _active_alerts_near(db, lat, lon)
        else:
            alerts = []
        text = settings.chat_helpline_message
        _log(db, channel=channel, question=question, answer_text=text, doc_ids=[],
             retrieval_score=None, is_evac=True, is_fallback=True)
        return {"answer": text, "sources": [], "alerts": alerts, "fallback": True}

    embedder = nlp_classifier._load_model()
    if embedder is None:
        text = settings.chat_helpline_message
        _log(db, channel=channel, question=question, answer_text=text, doc_ids=[],
             retrieval_score=None, is_evac=False, is_fallback=True)
        return {"answer": text, "sources": [], "alerts": [], "fallback": True}

    query_vec = embedder.encode([question], normalize_embeddings=True)[0].tolist()
    hits = retrieve(db, query_vec)
    best_score = hits[0][1] if hits else 0.0

    if best_score < settings.chat_retrieval_threshold:
        text = settings.chat_helpline_message
        _log(db, channel=channel, question=question, answer_text=text,
             doc_ids=[doc.id for doc, _ in hits], retrieval_score=best_score,
             is_evac=False, is_fallback=True)
        return {"answer": text, "sources": [], "alerts": [], "fallback": True}

    context = "\n\n".join(f"[{doc.title}]\n{doc.content}" for doc, _ in hits)
    user_message = f"Context:\n{context}\n\nQuestion: {question}"
    llm_answer = llm_mod.get_adapter().complete(SYSTEM_PROMPT, user_message)

    fallback = llm_answer is None
    text = llm_answer if llm_answer is not None else settings.chat_helpline_message
    sources = [] if fallback else [{"id": doc.id, "title": doc.title} for doc, _ in hits]
    _log(db, channel=channel, question=question, answer_text=text,
         doc_ids=[doc.id for doc, _ in hits], retrieval_score=best_score,
         is_evac=False, is_fallback=fallback)
    return {"answer": text, "sources": sources, "alerts": [], "fallback": fallback}
