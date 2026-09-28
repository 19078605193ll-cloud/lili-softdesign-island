"""Immutable catalog snapshots and content versions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260927_0011"
down_revision = "20260927_0010"
branch_labels = depends_on = None


def upgrade():
    op.add_column("knowledge_taxonomy_releases", sa.Column("catalog_snapshot", postgresql.JSONB(), nullable=True))
    op.add_column("question_import_batches", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("practice_questions", sa.Column("content_version", sa.Integer(), nullable=False, server_default="1"))
    connection = op.get_bind()
    invalid = connection.scalar(sa.text("SELECT count(*) FROM practice_question_parts WHERE position <= 0"))
    duplicate = connection.scalar(sa.text("SELECT count(*) FROM (SELECT 1 FROM practice_question_parts GROUP BY practice_question_id, position HAVING count(*) > 1) d"))
    if invalid or duplicate:
        raise RuntimeError("Invalid practice positions; diagnose before adding constraints")
    op.create_unique_constraint("uq_practice_part_position", "practice_question_parts", ["practice_question_id", "position"])
    op.create_check_constraint("ck_practice_part_position", "practice_question_parts", "position > 0")
    op.create_index("ix_asset_usage_asset", "question_asset_usages", ["asset_id"])


def downgrade():
    raise RuntimeError("Content versions are a write-safety boundary; restore a paired backup")
