"""Persist answers presented in the current recall round."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261013_0021"
down_revision = "20261012_0020"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "recall_sessions",
        sa.Column(
            "slot_presentations",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    # Existing retest rounds cannot be distinguished reliably in old submissions.
    # Leave those empty rather than exposing answers from an earlier round.
    op.execute("""
        WITH latest AS (
            SELECT DISTINCT ON (sub.session_id, sub.blank_key)
                sub.session_id, sub.blank_key, sub.answer, sub.correct
            FROM recall_submissions sub
            JOIN recall_sessions s ON s.id = sub.session_id
            WHERE s.phase IN ('first', 'retest_ready', 'completed')
            ORDER BY sub.session_id, sub.blank_key, sub.created_at DESC, sub.id DESC
        ), presentations AS (
            SELECT session_id, jsonb_object_agg(blank_key, jsonb_build_object(
                'value', answer, 'outcome', CASE WHEN correct THEN 'correct' ELSE 'incorrect' END
            )) AS values
            FROM latest GROUP BY session_id
        )
        UPDATE recall_sessions s SET slot_presentations = p.values
        FROM presentations p WHERE s.id = p.session_id
    """)


def downgrade():
    op.drop_column("recall_sessions", "slot_presentations")
