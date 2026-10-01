"""Record AI reservation time independently from step creation.

Legacy reservations cannot recover an unrecorded exact call timestamp. Preserve
their existing accounting date by backfilling created_at; all new reservations
record their own timestamp before the provider call.
"""

import sqlalchemy as sa
from alembic import op

revision = "8f341cab09ef"
down_revision = "31d9d4323a5f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("job_steps", sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE job_steps SET reserved_at = created_at WHERE reserved_tokens > 0")
    op.create_index("ix_job_steps_reserved_at", "job_steps", ["reserved_at"])


def downgrade():
    op.drop_index("ix_job_steps_reserved_at", table_name="job_steps")
    op.drop_column("job_steps", "reserved_at")
