"""Stable practice sources and durable related-practice requests."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260930_0015"
down_revision = "20260929_0014"
branch_labels = depends_on = None


def upgrade():
    op.add_column("learning_sessions", sa.Column("source_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_learning_session_resume",
        "learning_sessions",
        ["user_id", "subject_id", "source", "source_id"],
    )
    # Only infer paper sources when every stored question resolves to one paper.
    op.execute("""
        UPDATE learning_sessions s SET source_id = inferred.paper_id
        FROM (
            SELECT s.id, (array_agg(DISTINCT q.paper_id))[1] AS paper_id
            FROM learning_sessions s
            CROSS JOIN LATERAL jsonb_array_elements_text(s.question_ids) item(id)
            LEFT JOIN practice_questions q ON q.id::text = item.id
            JOIN exam_papers p ON p.id = q.paper_id AND p.subject_id = s.subject_id
            WHERE s.source = 'paper'
            GROUP BY s.id
            HAVING count(DISTINCT q.paper_id) = 1
               AND count(q.id) = jsonb_array_length(s.question_ids)
        ) inferred WHERE s.id = inferred.id
    """)
    op.execute("""
        UPDATE learning_sessions s SET source_id = q.id
        FROM practice_questions q
        WHERE s.source IN ('question', 'wrong', 'hesitant', 'favorite', 'similar')
          AND jsonb_array_length(s.question_ids) = 1
          AND s.question_ids->>0 = q.id::text
    """)
    op.create_table(
        "related_practice_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("learning_sessions.id"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            sa.Uuid(),
            sa.ForeignKey("practice_questions.id"),
            nullable=False,
        ),
        sa.Column("request_key", sa.String(200), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "request_key", name="uq_related_request_key"),
    )
    op.create_index(
        "ix_related_resume",
        "related_practice_requests",
        ["user_id", "session_id", "question_id"],
    )


def downgrade():
    raise RuntimeError("Restore a paired backup; refusing to discard learning history")
