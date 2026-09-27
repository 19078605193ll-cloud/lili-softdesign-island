"""Preserve unsplit composite explanations without copying them into each part."""
from alembic import op
import sqlalchemy as sa
revision = "20260926_0009"
down_revision = "20260925_0008"
branch_labels = depends_on = None

def upgrade():
    for table in ("question_groups", "question_import_groups"):
        op.add_column(table, sa.Column("explanation_markdown", sa.Text(), nullable=True))

def downgrade():
    raise RuntimeError("Refusing to discard shared explanations; restore a backup instead")
