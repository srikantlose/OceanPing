"""elevation_cells table + alerts.predicted_flooded_cells (phase 3, milestone 1: inundation model).

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-20
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB

from app.core.db import Base
from app import models  # noqa: F401 — ensure all tables are registered

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # create_all only creates tables that don't exist yet, so this adds
    # elevation_cells without touching the rest.
    Base.metadata.create_all(bind=bind)

    # Guarded: on a from-scratch database, 0002's create_all already built
    # `alerts` with this column (models.py isn't historically snapshotted per
    # migration) — see the phase-2 milestone-6 fix for the same bug class.
    existing_columns = {c["name"] for c in inspect(bind).get_columns("alerts")}
    if "predicted_flooded_cells" not in existing_columns:
        op.add_column(
            "alerts",
            sa.Column("predicted_flooded_cells", JSONB, nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    op.drop_column("alerts", "predicted_flooded_cells")
    op.drop_table("elevation_cells")
