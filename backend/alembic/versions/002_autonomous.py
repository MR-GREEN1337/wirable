"""autonomous scout — discovery + enrichment metadata on companies

Adds the columns the autonomous agency loop writes to:
  source, discovery_reason, founder_title, enrichment_confidence.

The `outbound_status` column already exists (created in 001) and is reused as
the pipeline state machine: discovered → auditing → audited → enriching →
contacted → replied → client.

All adds are idempotent (IF NOT EXISTS) so re-running on a partially-migrated
DB is safe.

Revision ID: 002
Revises: 001
Create Date: 2026-06-27

"""
from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent column adds — Postgres supports ADD COLUMN IF NOT EXISTS, which
    # lets this migration safely re-run even if a column was already created out
    # of band (e.g. via create_all drift).
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS source VARCHAR(32)")
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS discovery_reason TEXT")
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS founder_title VARCHAR(255)")
    op.execute(
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS enrichment_confidence DOUBLE PRECISION"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS enrichment_confidence")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS founder_title")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS discovery_reason")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS source")
