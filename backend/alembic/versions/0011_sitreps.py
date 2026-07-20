"""sitreps table (phase 3, milestone 2: auto-SITREPs).

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-20
"""
from alembic import op

from app.core.db import Base
from app import models  # noqa: F401 — ensure all tables are registered

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # create_all only creates tables that don't exist yet, so this adds
    # sitreps without touching the rest.
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    op.drop_table("sitreps")
