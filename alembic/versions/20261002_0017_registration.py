"""Self-service registration and user login timestamps."""

import sqlalchemy as sa

from alembic import op

revision = "20261002_0017"
down_revision = "20260930_0016"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("email", sa.String(254), nullable=True))
    op.add_column(
        "users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint("uq_users_email", "users", ["email"])


def downgrade():
    raise RuntimeError("Registration data cannot be removed by a lossy downgrade")
