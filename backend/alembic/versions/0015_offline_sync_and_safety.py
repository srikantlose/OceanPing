"""safety_checkins table + reports.client_key (phase 3, milestone 5: mobile
app with an offline-first queue).

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-21
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from app.core.db import Base
from app import models  # noqa: F401 — ensure all tables are registered

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # create_all only creates tables that don't exist yet, so this adds
    # safety_checkins without touching the rest.
    Base.metadata.create_all(bind=bind)

    # Guarded: on a from-scratch database an earlier migration's create_all()
    # already built `reports` with this column, since models.py isn't
    # historically snapshotted per migration (same bug class 0010/0012/0014
    # guard against).
    existing_columns = {c["name"] for c in inspect(bind).get_columns("reports")}
    if "client_key" not in existing_columns:
        op.add_column("reports", sa.Column("client_key", sa.String(64), nullable=True))
        op.create_unique_constraint("uq_reports_client_key", "reports", ["client_key"])


def downgrade() -> None:
    op.drop_constraint("uq_reports_client_key", "reports", type_="unique")
    op.drop_column("reports", "client_key")
    op.drop_table("safety_checkins")
