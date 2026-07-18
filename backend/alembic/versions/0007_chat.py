"""rag_documents + chat_logs for the phase-2 RAG chatbot.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-18
"""
from alembic import op

from app.core.db import Base
from app import models  # noqa: F401 — ensure all tables are registered

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # create_all only creates tables that don't exist yet (checkfirst=True default),
    # so this adds rag_documents/chat_logs without touching the rest.
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    op.drop_table("chat_logs")
    op.drop_table("rag_documents")
