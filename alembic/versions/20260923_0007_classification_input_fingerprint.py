"""Bind classification runs to the exact reviewed item content."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_0007"
down_revision: str | None = "20260923_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("question_classification_runs", sa.Column("input_fingerprint", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("question_classification_runs", "input_fingerprint")
