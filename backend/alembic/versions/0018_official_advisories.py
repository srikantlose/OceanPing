"""official_advisories table (phase 4, milestone 1: CAP + official interop).

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-23
"""
from alembic import op

from app.core.db import Base
from app import models  # noqa: F401 — ensure all tables are registered

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # New table only; create_all adds what's missing.
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    op.drop_table("official_advisories")
