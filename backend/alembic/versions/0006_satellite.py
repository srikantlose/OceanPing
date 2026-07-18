"""satellite_observations for the phase-2 satellite corroboration signal.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-18
"""
from alembic import op

from app.core.db import Base
from app import models  # noqa: F401 — ensure all tables are registered

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # create_all only creates tables that don't exist yet (checkfirst=True default),
    # so this adds satellite_observations without touching the rest.
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    op.drop_table("satellite_observations")
