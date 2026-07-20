"""Subscription.meta for web-push keys and other per-channel extras.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-17
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Guarded: on a from-scratch database, 0002's create_all already built
    # `subscriptions` with `meta` (models.py isn't historically snapshotted per
    # migration) — only an upgrade from a database that predates this column
    # needs the explicit ALTER.
    existing_columns = {c["name"] for c in inspect(bind).get_columns("subscriptions")}
    if "meta" not in existing_columns:
        op.add_column(
            "subscriptions",
            sa.Column("meta", JSONB, nullable=False, server_default="{}"),
        )


def downgrade() -> None:
    op.drop_column("subscriptions", "meta")
