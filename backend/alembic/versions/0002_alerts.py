"""Alerts, subscriptions, alert deliveries; reporters.role.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-07
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from app.core.db import Base
from app import models  # noqa: F401 — ensure all tables are registered

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Guarded: on a database that already has `reporters` (from 0001, before this
    # column existed), this adds it. On a from-scratch database, 0001's create_all
    # already built `reporters` with `role` (models.py isn't historically
    # snapshotted per migration), so the column is already there.
    existing_columns = {c["name"] for c in inspect(bind).get_columns("reporters")}
    if "role" not in existing_columns:
        op.add_column(
            "reporters",
            sa.Column("role", sa.String(16), nullable=False, server_default="citizen"),
        )
    # create_all only creates tables that don't exist yet (checkfirst=True default),
    # so this adds alerts / subscriptions / alert_deliveries without touching the rest.
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    op.drop_table("alert_deliveries")
    op.drop_table("subscriptions")
    op.drop_table("alerts")
    op.drop_column("reporters", "role")
