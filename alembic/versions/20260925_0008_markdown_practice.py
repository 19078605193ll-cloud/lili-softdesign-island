"""Markdown sources and explicit composite practice questions."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op
import uuid
from collections import defaultdict

revision = "20260925_0008"
down_revision = "20260923_0007"
branch_labels = depends_on = None


def upgrade():
    op.drop_constraint("uq_question_import_batches_published_paper_id", "question_import_batches", type_="unique")
    op.drop_constraint("uq_question_import_items_published_question_id", "question_import_items", type_="unique")
    op.alter_column("question_import_batches", "expected_question_count", nullable=True, server_default=None)
    op.alter_column("question_assets", "page_no", nullable=True)
    op.alter_column("question_assets", "bbox", nullable=True)
    op.drop_constraint("ck_question_source_type", "questions", type_="check")
    op.create_check_constraint("ck_question_source_type", "questions", "source_type IN ('official', 'recalled', 'practice', 'ai_generated')")
    # These software-designer papers are candidate recollections, not official releases.
    op.execute("UPDATE questions SET source_type='recalled' WHERE source_type='official' AND paper_id IS NOT NULL")
    op.create_table("practice_questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("paper_id", sa.Uuid(), sa.ForeignKey("exam_papers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("group_id", sa.Uuid(), sa.ForeignKey("question_groups.id", ondelete="RESTRICT")),
        sa.Column("source_label", sa.String(50), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("paper_id", "source_label", name="uq_practice_paper_label"))
    op.create_table("practice_question_parts",
        sa.Column("practice_question_id", sa.Uuid(), sa.ForeignKey("practice_questions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("question_id", sa.Uuid(), sa.ForeignKey("questions.id", ondelete="RESTRICT"), primary_key=True, unique=True),
        sa.Column("position", sa.Integer(), nullable=False))
    op.create_table("practice_question_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("practice_question_id", sa.Uuid(), sa.ForeignKey("practice_questions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("question_import_batches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    # Existing formal questions remain available through the new practice API.
    connection = op.get_bind()
    buckets = defaultdict(list)
    for row in connection.execute(sa.text("SELECT id, paper_id, group_id, question_no FROM questions WHERE paper_id IS NOT NULL ORDER BY question_no, id")).mappings():
        buckets[(row['paper_id'], row['group_id'] or row['id'])].append(row)
    for (paper_id, _), rows in buckets.items():
        unit_id = uuid.uuid4()
        numbers = [str(r['question_no']) for r in rows]
        label = numbers[0] if len(rows) == 1 else numbers[0] + '-' + numbers[-1]
        connection.execute(sa.text("INSERT INTO practice_questions(id,paper_id,group_id,source_label,content_hash) VALUES (:id,:paper,:group,:label,:hash)"),
            dict(id=unit_id, paper=paper_id, group=rows[0]['group_id'], label=label, hash='0'*64))
        for index, row in enumerate(rows,1):
            connection.execute(sa.text("INSERT INTO practice_question_parts(practice_question_id,question_id,position) VALUES (:unit,:question,:position)"),
                dict(unit=unit_id,question=row['id'],position=index))


def downgrade():
    # Refuse a lossy downgrade once the new workflow contains data.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM practice_questions")):
        raise RuntimeError("Export/remove Markdown practice data before downgrading this migration")
    op.drop_table("practice_question_revisions")
    op.drop_table("practice_question_parts")
    op.drop_table("practice_questions")
    op.execute("UPDATE question_import_batches SET expected_question_count=75 WHERE expected_question_count IS NULL")
    op.alter_column("question_import_batches", "expected_question_count", nullable=False, server_default="75")
    op.alter_column("question_assets", "page_no", nullable=False)
    op.alter_column("question_assets", "bbox", nullable=False)
    op.create_unique_constraint("uq_question_import_batches_published_paper_id", "question_import_batches", ["published_paper_id"])
    op.create_unique_constraint("uq_question_import_items_published_question_id", "question_import_items", ["published_question_id"])
    op.drop_constraint("ck_question_source_type", "questions", type_="check")
    op.create_check_constraint("ck_question_source_type", "questions", "source_type IN ('official', 'practice', 'ai_generated')")
