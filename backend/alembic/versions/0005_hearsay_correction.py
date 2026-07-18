"""training_examples.corrected_hazard_type for the reject-correction UI (phase 1, milestone 5).

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-18
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "training_examples",
        sa.Column("corrected_hazard_type", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("training_examples", "corrected_hazard_type")
