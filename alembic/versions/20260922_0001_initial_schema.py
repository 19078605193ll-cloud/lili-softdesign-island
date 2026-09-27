"""Initial data foundation schema.

Revision ID: 20260922_0001
Revises: None
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260922_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "exam_subjects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("qualification_level", sa.String(50), nullable=False),
        sa.Column("paper_kind", sa.String(50), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_exam_subject_code"),
    )

    op.create_table(
        "knowledge_taxonomy_releases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("source_name", sa.String(300), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["exam_subjects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject_id", "version", name="uq_taxonomy_release_version"),
    )

    op.create_table(
        "knowledge_nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("code", sa.String(160), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("node_type", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.String(100)), server_default=sa.text("'{}'::varchar[]"), nullable=False),
        sa.Column("keywords", postgresql.ARRAY(sa.String(100)), server_default=sa.text("'{}'::varchar[]"), nullable=False),
        sa.Column("classification_guidance", sa.Text(), server_default="", nullable=False),
        sa.Column("syllabus_refs", postgresql.ARRAY(sa.String(200)), server_default=sa.text("'{}'::varchar[]"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        *timestamps(),
        sa.CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_knowledge_node_not_self_parent"),
        sa.CheckConstraint("node_type IN ('chapter', 'module', 'topic')", name="ck_knowledge_node_type"),
        sa.CheckConstraint("status IN ('active', 'deprecated')", name="ck_knowledge_node_status"),
        sa.ForeignKeyConstraint(["subject_id"], ["exam_subjects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "subject_id", name="uq_knowledge_node_id_subject"),
        sa.UniqueConstraint("subject_id", "code", name="uq_knowledge_node_subject_code"),
    )
    op.create_foreign_key(
        "fk_knowledge_node_parent_subject",
        "knowledge_nodes",
        "knowledge_nodes",
        ["parent_id", "subject_id"],
        ["id", "subject_id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_knowledge_node_parent", "knowledge_nodes", ["parent_id"])
    op.create_index(
        "ix_knowledge_node_subject_type_status",
        "knowledge_nodes",
        ["subject_id", "node_type", "status"],
    )

    op.create_table(
        "exam_papers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(20), nullable=False),
        sa.Column("batch_code", sa.String(50), server_default="default", nullable=False),
        sa.Column("exam_date", sa.Date(), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("total_score", sa.Numeric(7, 2), server_default="0", nullable=False),
        *timestamps(),
        sa.CheckConstraint("period IN ('first_half', 'second_half', 'other')", name="ck_exam_paper_period"),
        sa.CheckConstraint("status IN ('draft', 'verified', 'retired')", name="ck_exam_paper_status"),
        sa.CheckConstraint("total_score >= 0", name="ck_exam_paper_total_score"),
        sa.ForeignKeyConstraint(["subject_id"], ["exam_subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "subject_id", name="uq_exam_paper_id_subject"),
        sa.UniqueConstraint("subject_id", "year", "period", "batch_code", name="uq_exam_paper_session"),
    )

    op.create_table(
        "questions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("paper_id", sa.Uuid(), nullable=True),
        sa.Column("question_no", sa.Integer(), nullable=True),
        sa.Column("question_type", sa.String(30), server_default="single_choice", nullable=False),
        sa.Column("stem_markdown", sa.Text(), nullable=False),
        sa.Column("explanation_markdown", sa.Text(), nullable=True),
        sa.Column("score", sa.Numeric(6, 2), server_default="1", nullable=False),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        *timestamps(),
        sa.CheckConstraint("question_type IN ('single_choice', 'multiple_choice')", name="ck_question_type"),
        sa.CheckConstraint("source_type IN ('official', 'practice', 'ai_generated')", name="ck_question_source_type"),
        sa.CheckConstraint("status IN ('draft', 'published', 'retired')", name="ck_question_status"),
        sa.CheckConstraint("score >= 0", name="ck_question_score"),
        sa.ForeignKeyConstraint(["subject_id"], ["exam_subjects.id"]),
        sa.ForeignKeyConstraint(
            ["paper_id", "subject_id"],
            ["exam_papers.id", "exam_papers.subject_id"],
            name="fk_question_paper_subject",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "subject_id", name="uq_question_id_subject"),
        sa.UniqueConstraint("paper_id", "question_no", name="uq_question_paper_number"),
    )
    op.create_index("ix_question_subject_status", "questions", ["subject_id", "status"])
    op.create_index("ix_question_content_hash", "questions", ["content_hash"])

    op.create_table(
        "question_options",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("option_key", sa.String(10), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.CheckConstraint("sort_order >= 0", name="ck_question_option_sort_order"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "question_id", name="uq_question_option_id_question"),
        sa.UniqueConstraint("question_id", "option_key", name="uq_question_option_key"),
    )

    op.create_table(
        "question_correct_options",
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("option_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["option_id", "question_id"],
            ["question_options.id", "question_options.question_id"],
            name="fk_correct_option_question",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("question_id", "option_id"),
    )

    op.create_table(
        "question_knowledge_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_node_id", sa.Uuid(), nullable=False),
        sa.Column("taxonomy_release_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), server_default="proposed", nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("candidate_rank", sa.Integer(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        *timestamps(),
        sa.CheckConstraint("role IN ('primary', 'related')", name="ck_assignment_role"),
        sa.CheckConstraint("status IN ('proposed', 'confirmed', 'rejected')", name="ck_assignment_status"),
        sa.CheckConstraint("source IN ('manual', 'ai', 'import')", name="ck_assignment_source"),
        sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_assignment_confidence"),
        sa.CheckConstraint("candidate_rank IS NULL OR candidate_rank > 0", name="ck_assignment_rank"),
        sa.ForeignKeyConstraint(
            ["question_id", "subject_id"],
            ["questions.id", "questions.subject_id"],
            name="fk_assignment_question_subject",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_node_id", "subject_id"],
            ["knowledge_nodes.id", "knowledge_nodes.subject_id"],
            name="fk_assignment_node_subject",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["taxonomy_release_id"], ["knowledge_taxonomy_releases.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("question_id", "knowledge_node_id", "role", "status", name="uq_assignment_candidate"),
    )
    op.create_index(
        "uq_assignment_confirmed_primary",
        "question_knowledge_assignments",
        ["question_id"],
        unique=True,
        postgresql_where=sa.text("role = 'primary' AND status = 'confirmed'"),
    )
    op.create_index(
        "ix_assignment_node_status",
        "question_knowledge_assignments",
        ["knowledge_node_id", "status"],
    )

    op.create_table(
        "knowledge_exam_aggregates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_node_id", sa.Uuid(), nullable=False),
        sa.Column("exam_paper_id", sa.Uuid(), nullable=False),
        sa.Column("primary_question_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("related_question_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("primary_score_total", sa.Numeric(8, 2), server_default="0", nullable=False),
        sa.Column("calculation_version", sa.String(50), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("primary_question_count >= 0", name="ck_aggregate_primary_count"),
        sa.CheckConstraint("related_question_count >= 0", name="ck_aggregate_related_count"),
        sa.CheckConstraint("primary_score_total >= 0", name="ck_aggregate_primary_score"),
        sa.ForeignKeyConstraint(
            ["knowledge_node_id", "subject_id"],
            ["knowledge_nodes.id", "knowledge_nodes.subject_id"],
            name="fk_aggregate_node_subject",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["exam_paper_id", "subject_id"],
            ["exam_papers.id", "exam_papers.subject_id"],
            name="fk_aggregate_paper_subject",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("knowledge_node_id", "exam_paper_id", name="uq_aggregate_node_paper"),
    )


def downgrade() -> None:
    op.drop_table("knowledge_exam_aggregates")
    op.drop_index("ix_assignment_node_status", table_name="question_knowledge_assignments")
    op.drop_index("uq_assignment_confirmed_primary", table_name="question_knowledge_assignments")
    op.drop_table("question_knowledge_assignments")
    op.drop_table("question_correct_options")
    op.drop_table("question_options")
    op.drop_index("ix_question_content_hash", table_name="questions")
    op.drop_index("ix_question_subject_status", table_name="questions")
    op.drop_table("questions")
    op.drop_table("exam_papers")
    op.drop_index("ix_knowledge_node_subject_type_status", table_name="knowledge_nodes")
    op.drop_index("ix_knowledge_node_parent", table_name="knowledge_nodes")
    op.drop_table("knowledge_nodes")
    op.drop_table("knowledge_taxonomy_releases")
    op.drop_table("exam_subjects")
