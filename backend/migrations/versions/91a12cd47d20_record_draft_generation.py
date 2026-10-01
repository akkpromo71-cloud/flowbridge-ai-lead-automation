"""Record generator provenance on the immutable message version."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "91a12cd47d20"
down_revision = "8f341cab09ef"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "message_versions",
        sa.Column(
            "generation", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )


def downgrade():
    op.drop_column("message_versions", "generation")
