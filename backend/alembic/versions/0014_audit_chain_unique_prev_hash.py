"""Unique index on audit_log.prev_hash — makes a forked audit chain
structurally impossible to persist (see scoring/audit.py::append_audit).

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-21
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_audit_log_prev_hash"

FIND_FORKS_SQL = """
SELECT prev_hash, count(*) AS n, min(id) AS first_id, max(id) AS last_id
FROM audit_log
GROUP BY prev_hash
HAVING count(*) > 1
ORDER BY min(id)
"""


def upgrade() -> None:
    bind = op.get_bind()

    # A chain that already forked (written before append_audit took an
    # advisory lock) can't accept this constraint. Fail with something
    # actionable rather than a bare IntegrityError.
    #
    # Deliberately no auto-repair: recomputing the stored hashes to make the
    # chain "verify" again is precisely the capability a tamper-evident log
    # exists to deny, so it isn't something this project ships. A fork is a
    # real, permanent loss of that guarantee for the affected range; the
    # honest remedies are to keep it (leaving verify_chain reporting broken,
    # with the reason known) or to truncate the log and restart the chain
    # from genesis, and that's an operator's call, not a migration's.
    forks = bind.execute(sa.text(FIND_FORKS_SQL)).fetchall()
    if forks:
        detail = "; ".join(f"prev_hash {f.prev_hash[:12]}… shared by ids {f.first_id}..{f.last_id}" for f in forks)
        raise RuntimeError(
            f"audit_log has {len(forks)} forked chain point(s) predating the append_audit "
            f"concurrency fix, so a UNIQUE index on prev_hash cannot be created: {detail}. "
            "These rows are genuinely corrupt — the chain's tamper-evidence is void across "
            "them and no migration can honestly restore it. Decide explicitly: keep them "
            "(and accept verify_chain reporting the chain broken), or, if this is a "
            "development database whose audit history carries no evidentiary weight, "
            "`TRUNCATE audit_log;` to restart the chain from genesis, then re-run this migration."
        )

    # Guarded: on a from-scratch database an earlier migration's
    # Base.metadata.create_all() already built this index, since models.py
    # isn't historically snapshotted per migration (same bug class 0010/0012
    # guard against for their added columns).
    existing = {ix["name"] for ix in inspect(bind).get_indexes("audit_log")}
    if INDEX_NAME not in existing:
        op.create_index(INDEX_NAME, "audit_log", ["prev_hash"], unique=True)


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="audit_log")
