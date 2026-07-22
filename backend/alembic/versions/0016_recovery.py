"""damage_assessments, relief_requests, aid_offers, missing_persons tables
(phase 3, milestone 7: post-disaster recovery module).

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-22
"""
from alembic import op

from app.core.db import Base
from app import models  # noqa: F401 — ensure all tables are registered

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # All four tables are new; create_all only adds what's missing.
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    op.drop_table("missing_persons")
    op.drop_table("aid_offers")
    op.drop_table("relief_requests")
    op.drop_table("damage_assessments")
