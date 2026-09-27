"""Add question import, review, and publishing workflow.

Revision ID: 20260922_0002
Revises: 20260922_0001
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260922_0002"
down_revision: str | None = "20260922_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "question_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("paper_id", sa.Uuid(), nullable=False),
        sa.Column("source_label", sa.String(50), nullable=False),
        sa.Column("material_markdown", sa.Text(), server_default="", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        *timestamps(),
        sa.CheckConstraint("sort_order >= 0", name="ck_question_group_sort_order"),
        sa.ForeignKeyConstraint(
            ["paper_id", "subject_id"],
            ["exam_papers.id", "exam_papers.subject_id"],
            name="fk_question_group_paper_subject",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "subject_id", "paper_id", name="uq_question_group_id_subject_paper"),
        sa.UniqueConstraint("paper_id", "source_label", name="uq_question_group_paper_label"),
    )
    op.add_column("questions", sa.Column("group_id", sa.Uuid(), nullable=True))
    op.add_column("questions", sa.Column("group_order", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_question_group_subject_paper",
        "questions",
        "question_groups",
        ["group_id", "subject_id", "paper_id"],
        ["id", "subject_id", "paper_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "question_import_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("taxonomy_release_id", sa.Uuid(), nullable=False),
        sa.Column("published_paper_id", sa.Uuid(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(20), nullable=False),
        sa.Column("batch_code", sa.String(50), server_default="default", nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("exam_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(30), server_default="uploaded", nullable=False),
        sa.Column("parser_version", sa.String(50), server_default="vision-v1", nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        *timestamps(),
        sa.CheckConstraint(
            "status IN ('uploaded', 'sectioned', 'parsed', 'in_review', "
            "'ready_to_publish', 'published', 'failed')",
            name="ck_import_batch_status",
        ),
        sa.CheckConstraint("period IN ('first_half', 'second_half', 'other')", name="ck_import_batch_period"),
        sa.CheckConstraint("year >= 2000 AND year <= 2100", name="ck_import_batch_year"),
        sa.ForeignKeyConstraint(["subject_id"], ["exam_subjects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["taxonomy_release_id"], ["knowledge_taxonomy_releases.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["published_paper_id"], ["exam_papers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("published_paper_id", name="uq_question_import_batches_published_paper_id"),
    )
    op.create_index(
        "ix_import_batch_subject_status", "question_import_batches", ["subject_id", "status"]
    )

    op.create_table(
        "question_source_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(20), server_default="combined", nullable=False),
        sa.Column("original_name", sa.String(300), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("page_selections", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(20), server_default="uploaded", nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        *timestamps(),
        sa.CheckConstraint("role IN ('combined', 'questions', 'answers')", name="ck_source_document_role"),
        sa.CheckConstraint("status IN ('uploaded', 'rendered', 'failed')", name="ck_source_document_status"),
        sa.CheckConstraint("file_size > 0", name="ck_source_document_file_size"),
        sa.CheckConstraint("page_count IS NULL OR page_count > 0", name="ck_source_document_page_count"),
        sa.ForeignKeyConstraint(["batch_id"], ["question_import_batches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "sha256", name="uq_source_document_batch_hash"),
    )

    op.create_table(
        "question_source_page_extractions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("raw_response", postgresql.JSONB(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        *timestamps(),
        sa.CheckConstraint("role IN ('questions', 'answers')", name="ck_page_extraction_role"),
        sa.CheckConstraint("status IN ('pending', 'completed', 'failed')", name="ck_page_extraction_status"),
        sa.CheckConstraint("page_no > 0", name="ck_page_extraction_page_no"),
        sa.ForeignKeyConstraint(["document_id"], ["question_source_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "page_no", "role", "prompt_version", name="uq_page_extraction_version"
        ),
    )

    op.create_table(
        "question_import_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("source_label", sa.String(50), nullable=False),
        sa.Column("material_markdown", sa.Text(), server_default="", nullable=False),
        sa.Column("source_refs", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        *timestamps(),
        sa.CheckConstraint("sort_order >= 0", name="ck_import_group_sort_order"),
        sa.ForeignKeyConstraint(["batch_id"], ["question_import_batches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "source_label", name="uq_import_group_batch_label"),
    )

    op.create_table(
        "question_import_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=True),
        sa.Column("published_question_id", sa.Uuid(), nullable=True),
        sa.Column("question_no", sa.Integer(), nullable=False),
        sa.Column("question_type", sa.String(30), server_default="single_choice", nullable=False),
        sa.Column("stem_markdown", sa.Text(), server_default="", nullable=False),
        sa.Column("options_payload", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("correct_option_keys", postgresql.ARRAY(sa.String(10)), server_default=sa.text("'{}'::varchar[]"), nullable=False),
        sa.Column("explanation_markdown", sa.Text(), nullable=True),
        sa.Column("score", sa.Numeric(6, 2), server_default="1", nullable=False),
        sa.Column("source_refs", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("validation_issues", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("status", sa.String(20), server_default="needs_review", nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
        *timestamps(),
        sa.CheckConstraint("question_no > 0", name="ck_import_item_question_no"),
        sa.CheckConstraint("score >= 0", name="ck_import_item_score"),
        sa.CheckConstraint("question_type IN ('single_choice', 'multiple_choice')", name="ck_import_item_type"),
        sa.CheckConstraint(
            "status IN ('needs_review', 'approved', 'rejected', 'blocked', 'published')",
            name="ck_import_item_status",
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["question_import_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["group_id"], ["question_import_groups.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["published_question_id"], ["questions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "question_no", name="uq_import_item_batch_number"),
        sa.UniqueConstraint("published_question_id", name="uq_question_import_items_published_question_id"),
    )
    op.create_index(
        "ix_import_item_batch_status", "question_import_items", ["batch_id", "status"]
    )

    op.create_table(
        "question_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("bbox", postgresql.JSONB(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("mime_type", sa.String(100), server_default="image/png", nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        *timestamps(),
        sa.CheckConstraint("page_no > 0", name="ck_question_asset_page_no"),
        sa.CheckConstraint("width > 0 AND height > 0", name="ck_question_asset_dimensions"),
        sa.CheckConstraint("kind IN ('figure', 'table', 'code', 'source_snapshot')", name="ck_question_asset_kind"),
        sa.ForeignKeyConstraint(["document_id"], ["question_source_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "page_no", "sha256", name="uq_question_asset_source_hash"),
    )

    op.create_table(
        "question_classification_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("import_item_id", sa.Uuid(), nullable=False),
        sa.Column("taxonomy_release_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("catalog_checksum", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("raw_response", postgresql.JSONB(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        *timestamps(),
        sa.CheckConstraint("status IN ('pending', 'completed', 'failed')", name="ck_classification_run_status"),
        sa.ForeignKeyConstraint(["import_item_id"], ["question_import_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["taxonomy_release_id"], ["knowledge_taxonomy_releases.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_classification_run_item_status",
        "question_classification_runs",
        ["import_item_id", "status"],
    )

    op.create_table(
        "question_classification_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_node_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("rationale", sa.Text(), server_default="", nullable=False),
        sa.CheckConstraint("role IN ('primary', 'related')", name="ck_classification_candidate_role"),
        sa.CheckConstraint("rank > 0", name="ck_classification_candidate_rank"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_classification_candidate_confidence"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["question_classification_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_node_id"], ["knowledge_nodes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "knowledge_node_id", "role", name="uq_classification_candidate"),
    )

    op.create_table(
        "question_import_knowledge_selections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("import_item_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_node_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        *timestamps(),
        sa.CheckConstraint("role IN ('primary', 'related')", name="ck_import_selection_role"),
        sa.CheckConstraint("source IN ('manual', 'ai')", name="ck_import_selection_source"),
        sa.ForeignKeyConstraint(["import_item_id"], ["question_import_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_node_id"], ["knowledge_nodes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["question_classification_candidates.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "import_item_id", "knowledge_node_id", "role", name="uq_import_selection_node_role"
        ),
    )
    op.create_index(
        "uq_import_selection_primary",
        "question_import_knowledge_selections",
        ["import_item_id"],
        unique=True,
        postgresql_where=sa.text("role = 'primary'"),
    )


def downgrade() -> None:
    op.drop_index("uq_import_selection_primary", table_name="question_import_knowledge_selections")
    op.drop_table("question_import_knowledge_selections")
    op.drop_table("question_classification_candidates")
    op.drop_index("ix_classification_run_item_status", table_name="question_classification_runs")
    op.drop_table("question_classification_runs")
    op.drop_table("question_assets")
    op.drop_index("ix_import_item_batch_status", table_name="question_import_items")
    op.drop_table("question_import_items")
    op.drop_table("question_import_groups")
    op.drop_table("question_source_page_extractions")
    op.drop_table("question_source_documents")
    op.drop_index("ix_import_batch_subject_status", table_name="question_import_batches")
    op.drop_table("question_import_batches")
    op.drop_constraint("fk_question_group_subject_paper", "questions", type_="foreignkey")
    op.drop_column("questions", "group_order")
    op.drop_column("questions", "group_id")
    op.drop_table("question_groups")
