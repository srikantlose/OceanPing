"""reports.processing_stage, reports.hazard_locked (phase 3, milestone 8: the
architecture split's bus pipeline mode — see modules/ingest/bus.py).

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-22
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Guarded per the usual rule: on a from-scratch database, 0001's
    # create_all already built `reports` with these columns (models.py isn't
    # historically snapshotted per migration), so this only fires on a
    # database that already had `reports` before this milestone.
    existing_columns = {c["name"] for c in inspect(bind).get_columns("reports")}
    if "processing_stage" not in existing_columns:
        op.add_column(
            "reports",
            sa.Column("processing_stage", sa.String(16), nullable=False, server_default="scored"),
        )
        op.create_index("ix_reports_processing_stage", "reports", ["processing_stage"])
    if "hazard_locked" not in existing_columns:
        op.add_column(
            "reports",
            sa.Column("hazard_locked", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    op.drop_index("ix_reports_processing_stage", table_name="reports")
    op.drop_column("reports", "processing_stage")
    op.drop_column("reports", "hazard_locked")
