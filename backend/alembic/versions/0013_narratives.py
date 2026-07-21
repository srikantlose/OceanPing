"""narratives + narrative_deliveries tables (phase 3, milestone 4: rumor
tracker + alert drafting).

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-21
"""
from alembic import op

from app.core.db import Base
from app import models  # noqa: F401 — ensure all tables are registered

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # create_all only creates tables that don't exist yet, so this adds
    # narratives/narrative_deliveries without touching the rest.
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    op.drop_table("narrative_deliveries")
    op.drop_table("narratives")
