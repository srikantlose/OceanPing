"""api_keys, dataset_releases tables + reports.location_anonymized_at
(phase 4, milestone 3: open-data pipeline with DP + retention jobs).

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from app.core.db import Base
from app import models  # noqa: F401 — ensure all tables are registered

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # New tables only; create_all adds what's missing.
    Base.metadata.create_all(bind=bind)

    # Guarded per the usual rule: on a from-scratch database an earlier
    # migration's create_all() already built `reports` with this column,
    # since models.py isn't historically snapshotted per migration.
    existing_columns = {c["name"] for c in inspect(bind).get_columns("reports")}
    if "location_anonymized_at" not in existing_columns:
        op.add_column(
            "reports",
            sa.Column("location_anonymized_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("reports", "location_anonymized_at")
    op.drop_table("dataset_releases")
    op.drop_table("api_keys")
