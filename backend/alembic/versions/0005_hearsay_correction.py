"""training_examples.corrected_hazard_type for the reject-correction UI (phase 1, milestone 5).

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-18
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Guarded: on a from-scratch database, 0004's create_all already built
    # `training_examples` with `corrected_hazard_type` (models.py isn't
    # historically snapshotted per migration) — only an upgrade from a database
    # that predates this column needs the explicit ALTER.
    existing_columns = {c["name"] for c in inspect(bind).get_columns("training_examples")}
    if "corrected_hazard_type" not in existing_columns:
        op.add_column(
            "training_examples",
            sa.Column("corrected_hazard_type", sa.String(32), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("training_examples", "corrected_hazard_type")
