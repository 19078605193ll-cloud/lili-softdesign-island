"""Optional persistent user avatar reference."""

from alembic import op
import sqlalchemy as sa

revision = "20260930_0016"
down_revision = "20260930_0015"
branch_labels = depends_on = None


def upgrade():
    op.add_column("users", sa.Column("avatar_key", sa.String(100), nullable=True))


def downgrade():
    raise RuntimeError("Restore a paired backup to preserve profile information")
