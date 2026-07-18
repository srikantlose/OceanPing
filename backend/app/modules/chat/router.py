"""Public RAG chatbot endpoint - no analyst auth, same trust boundary as
/reports and /map/*. Every question is logged to chat_logs regardless of
which path (evacuation bypass, retrieval fallback, or a real LLM answer) it
took."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.chat.service import answer as answer_question

router = APIRouter(tags=["chat"])


class ChatIn(BaseModel):
    question: str
    lat: float | None = None
    lon: float | None = None


@router.post("/chat")
def chat(body: ChatIn, db: Session = Depends(get_db)) -> dict:
    return answer_question(db, body.question, channel="web", lat=body.lat, lon=body.lon)
