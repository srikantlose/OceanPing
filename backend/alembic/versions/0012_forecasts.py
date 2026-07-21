"""forecasts table + alerts.projected_cells + sensor_readings_hourly continuous
aggregate (phase 3, milestone 3: forecasting + propagation pre-alerts).

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-20
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB

from app.core.db import Base
from app import models  # noqa: F401 — ensure all tables are registered

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# Real Timescale continuous aggregate (not a plain view) over the
# sensor_readings hypertable created in 0001 — hourly per-station/variable
# stats, the input series modules/forecast/service.py fits its harmonic-trend
# regression against. Real-time aggregation (Timescale's default for a
# `timescaledb.continuous` view) means querying it also reflects any raw
# reading newer than the last policy-driven refresh, so a forecast generated
# moments after startup still sees fresh data.
CONTINUOUS_AGGREGATE_SQL = """
CREATE MATERIALIZED VIEW sensor_readings_hourly
WITH (timescaledb.continuous) AS
SELECT
    station_id,
    variable,
    time_bucket('1 hour', time) AS bucket,
    avg(value) AS avg_value,
    min(value) AS min_value,
    max(value) AS max_value,
    count(*) AS n
FROM sensor_readings
GROUP BY station_id, variable, bucket
"""

CONTINUOUS_AGGREGATE_POLICY_SQL = """
SELECT add_continuous_aggregate_policy('sensor_readings_hourly',
    start_offset => INTERVAL '8 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour')
"""


def upgrade() -> None:
    bind = op.get_bind()
    # create_all only creates tables that don't exist yet, so this adds
    # forecasts without touching the rest.
    Base.metadata.create_all(bind=bind)

    # Guarded: on a from-scratch database, 0002's create_all already built
    # `alerts` with this column (models.py isn't historically snapshotted per
    # migration) — see 0010's identical guard for the same bug class.
    existing_columns = {c["name"] for c in inspect(bind).get_columns("alerts")}
    if "projected_cells" not in existing_columns:
        op.add_column(
            "alerts",
            sa.Column("projected_cells", JSONB, nullable=False, server_default="[]"),
        )

    # Timescale continuous aggregate creation (and its refresh policy) can't
    # run inside a transaction block — autocommit_block() commits the
    # migration's transaction so far, runs these in autocommit mode, then
    # opens a fresh one for anything after.
    with op.get_context().autocommit_block():
        op.execute(CONTINUOUS_AGGREGATE_SQL)
        op.execute(CONTINUOUS_AGGREGATE_POLICY_SQL)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS sensor_readings_hourly CASCADE")
    op.drop_column("alerts", "projected_cells")
    op.drop_table("forecasts")
