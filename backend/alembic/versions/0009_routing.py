"""shelters for the phase-2 evacuation routing milestone.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-20
"""
from alembic import op

from app.core.db import Base
from app import models  # noqa: F401 — ensure all tables are registered

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # create_all only creates tables that don't exist yet (checkfirst=True default),
    # so this adds shelters without touching the rest.
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    op.drop_table("shelters")
