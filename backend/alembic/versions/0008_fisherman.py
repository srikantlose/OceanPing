"""pfz_advisories for the phase-2 fisherman-mode PFZ surface.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-18
"""
from alembic import op

from app.core.db import Base
from app import models  # noqa: F401 — ensure all tables are registered

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # create_all only creates tables that don't exist yet (checkfirst=True default),
    # so this adds pfz_advisories without touching the rest.
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    op.drop_table("pfz_advisories")
