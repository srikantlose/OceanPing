"""training_examples + model_versions for the active-learning loop.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-18
"""
from alembic import op

from app.core.db import Base
from app import models  # noqa: F401 — ensure all tables are registered


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # create_all only creates tables that don't exist yet (checkfirst=True default),
    # so this adds training_examples / model_versions without touching the rest.
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    op.drop_table("model_versions")
    op.drop_table("training_examples")
