"""Add batch validation metadata and auditable automatic repair runs.

Revision ID: 20260922_0004
Revises: 20260922_0003
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260922_0004"
down_revision: str | None = "20260922_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "question_import_batches",
        sa.Column(
            "expected_question_count",
            sa.Integer(),
            server_default="75",
            nullable=False,
        ),
    )
    op.add_column(
        "question_import_batches",
        sa.Column(
            "validation_summary",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_import_batch_expected_question_count",
        "question_import_batches",
        "expected_question_count > 0",
    )
    op.create_table(
        "question_import_repair_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("page_numbers", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("question_numbers", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("raw_response", postgresql.JSONB(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('questions', 'answers')", name="ck_import_repair_role"),
        sa.CheckConstraint("attempt > 0 AND attempt <= 2", name="ck_import_repair_attempt"),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_import_repair_status",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["question_import_batches.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["question_source_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_import_repair_batch_status",
        "question_import_repair_runs",
        ["batch_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_import_repair_batch_status", table_name="question_import_repair_runs")
    op.drop_table("question_import_repair_runs")
    op.drop_constraint(
        "ck_import_batch_expected_question_count",
        "question_import_batches",
        type_="check",
    )
    op.drop_column("question_import_batches", "validation_summary")
    op.drop_column("question_import_batches", "expected_question_count")
