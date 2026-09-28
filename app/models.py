import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ExamSubject(TimestampMixin, Base):
    __tablename__ = "exam_subjects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    qualification_level: Mapped[str] = mapped_column(String(50), nullable=False)
    paper_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class KnowledgeTaxonomyRelease(Base):
    __tablename__ = "knowledge_taxonomy_releases"
    catalog_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    __table_args__ = (UniqueConstraint("subject_id", "version", name="uq_taxonomy_release_version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("exam_subjects.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    source_name: Mapped[str] = mapped_column(String(300), nullable=False)
    source_reference: Mapped[str] = mapped_column(Text, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KnowledgeNode(TimestampMixin, Base):
    __tablename__ = "knowledge_nodes"
    __table_args__ = (
        UniqueConstraint("subject_id", "code", name="uq_knowledge_node_subject_code"),
        UniqueConstraint("id", "subject_id", name="uq_knowledge_node_id_subject"),
        ForeignKeyConstraint(
            ["parent_id", "subject_id"],
            ["knowledge_nodes.id", "knowledge_nodes.subject_id"],
            name="fk_knowledge_node_parent_subject",
            ondelete="RESTRICT",
        ),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_knowledge_node_not_self_parent"),
        CheckConstraint("node_type IN ('chapter', 'module', 'topic')", name="ck_knowledge_node_type"),
        CheckConstraint("status IN ('active', 'deprecated')", name="ck_knowledge_node_status"),
        Index("ix_knowledge_node_parent", "parent_id"),
        Index("ix_knowledge_node_subject_type_status", "subject_id", "node_type", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("exam_subjects.id", ondelete="RESTRICT"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    code: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    node_type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, default=list, server_default=text("'{}'::varchar[]")
    )
    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, default=list, server_default=text("'{}'::varchar[]")
    )
    classification_guidance: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    syllabus_refs: Mapped[list[str]] = mapped_column(
        ARRAY(String(200)), nullable=False, default=list, server_default=text("'{}'::varchar[]")
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")


class ExamPaper(TimestampMixin, Base):
    __tablename__ = "exam_papers"
    __table_args__ = (
        UniqueConstraint("id", "subject_id", name="uq_exam_paper_id_subject"),
        UniqueConstraint(
            "subject_id", "year", "period", "batch_code", name="uq_exam_paper_session"
        ),
        CheckConstraint("period IN ('first_half', 'second_half', 'other')", name="ck_exam_paper_period"),
        CheckConstraint("status IN ('draft', 'verified', 'retired')", name="ck_exam_paper_status"),
        CheckConstraint("total_score >= 0", name="ck_exam_paper_total_score"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("exam_subjects.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    batch_code: Mapped[str] = mapped_column(String(50), nullable=False, default="default", server_default="default")
    exam_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    total_score: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False, default=0, server_default="0")


class QuestionGroup(TimestampMixin, Base):
    __tablename__ = "question_groups"
    __table_args__ = (
        ForeignKeyConstraint(
            ["paper_id", "subject_id"],
            ["exam_papers.id", "exam_papers.subject_id"],
            name="fk_question_group_paper_subject",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "subject_id", "paper_id", name="uq_question_group_id_subject_paper"),
        UniqueConstraint("paper_id", "source_label", name="uq_question_group_paper_label"),
        CheckConstraint("sort_order >= 0", name="ck_question_group_sort_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    paper_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_label: Mapped[str] = mapped_column(String(50), nullable=False)
    explanation_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    material_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class Question(TimestampMixin, Base):
    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint("id", "subject_id", name="uq_question_id_subject"),
        UniqueConstraint("paper_id", "question_no", name="uq_question_paper_number"),
        ForeignKeyConstraint(
            ["paper_id", "subject_id"],
            ["exam_papers.id", "exam_papers.subject_id"],
            name="fk_question_paper_subject",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["group_id", "subject_id", "paper_id"],
            ["question_groups.id", "question_groups.subject_id", "question_groups.paper_id"],
            name="fk_question_group_subject_paper",
            ondelete="RESTRICT",
        ),
        CheckConstraint("question_type IN ('single_choice', 'multiple_choice')", name="ck_question_type"),
        CheckConstraint("source_type IN ('official', 'recalled', 'practice', 'ai_generated')", name="ck_question_source_type"),
        CheckConstraint("status IN ('draft', 'published', 'retired')", name="ck_question_status"),
        CheckConstraint("score >= 0", name="ck_question_score"),
        Index("ix_question_subject_status", "subject_id", "status"),
        Index("ix_question_content_hash", "content_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("exam_subjects.id"), nullable=False)
    paper_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    group_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    question_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    question_type: Mapped[str] = mapped_column(String(30), nullable=False, default="single_choice")
    stem_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    explanation_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=1, server_default="1")
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")


class QuestionOption(Base):
    __tablename__ = "question_options"
    __table_args__ = (
        UniqueConstraint("question_id", "option_key", name="uq_question_option_key"),
        UniqueConstraint("id", "question_id", name="uq_question_option_id_question"),
        CheckConstraint("sort_order >= 0", name="ck_question_option_sort_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    question_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    option_key: Mapped[str] = mapped_column(String(10), nullable=False)
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)


class QuestionCorrectOption(Base):
    __tablename__ = "question_correct_options"
    __table_args__ = (
        ForeignKeyConstraint(
            ["option_id", "question_id"],
            ["question_options.id", "question_options.question_id"],
            name="fk_correct_option_question",
            ondelete="CASCADE",
        ),
    )

    question_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    option_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)


class QuestionKnowledgeAssignment(TimestampMixin, Base):
    __tablename__ = "question_knowledge_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["question_id", "subject_id"],
            ["questions.id", "questions.subject_id"],
            name="fk_assignment_question_subject",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["knowledge_node_id", "subject_id"],
            ["knowledge_nodes.id", "knowledge_nodes.subject_id"],
            name="fk_assignment_node_subject",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "question_id", "knowledge_node_id", "role", "status", name="uq_assignment_candidate"
        ),
        CheckConstraint("role IN ('primary', 'related')", name="ck_assignment_role"),
        CheckConstraint("status IN ('proposed', 'confirmed', 'rejected')", name="ck_assignment_status"),
        CheckConstraint("source IN ('manual', 'ai', 'import')", name="ck_assignment_source"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_assignment_confidence"),
        CheckConstraint("candidate_rank IS NULL OR candidate_rank > 0", name="ck_assignment_rank"),
        Index(
            "uq_assignment_confirmed_primary",
            "question_id",
            unique=True,
            postgresql_where=text("role = 'primary' AND status = 'confirmed'"),
        ),
        Index("ix_assignment_node_status", "knowledge_node_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    question_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    knowledge_node_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    taxonomy_release_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("knowledge_taxonomy_releases.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed")
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    candidate_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgeExamAggregate(Base):
    __tablename__ = "knowledge_exam_aggregates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["knowledge_node_id", "subject_id"],
            ["knowledge_nodes.id", "knowledge_nodes.subject_id"],
            name="fk_aggregate_node_subject",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["exam_paper_id", "subject_id"],
            ["exam_papers.id", "exam_papers.subject_id"],
            name="fk_aggregate_paper_subject",
            ondelete="CASCADE",
        ),
        UniqueConstraint("knowledge_node_id", "exam_paper_id", name="uq_aggregate_node_paper"),
        CheckConstraint("primary_question_count >= 0", name="ck_aggregate_primary_count"),
        CheckConstraint("related_question_count >= 0", name="ck_aggregate_related_count"),
        CheckConstraint("primary_score_total >= 0", name="ck_aggregate_primary_score"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    knowledge_node_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    exam_paper_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    primary_question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    related_question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    primary_score_total: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=0, server_default="0")
    calculation_version: Mapped[str] = mapped_column(String(50), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class QuestionImportBatch(TimestampMixin, Base):
    __tablename__ = "question_import_batches"
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    __table_args__ = (
        CheckConstraint(
            "status IN ('uploaded', 'sectioned', 'parsed', 'in_review', "
            "'ready_to_publish', 'published', 'failed')",
            name="ck_import_batch_status",
        ),
        CheckConstraint("period IN ('first_half', 'second_half', 'other')", name="ck_import_batch_period"),
        CheckConstraint("year >= 2000 AND year <= 2100", name="ck_import_batch_year"),
        CheckConstraint(
            "expected_question_count > 0",
            name="ck_import_batch_expected_question_count",
        ),
        Index("ix_import_batch_subject_status", "subject_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("exam_subjects.id", ondelete="RESTRICT"), nullable=False
    )
    taxonomy_release_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_taxonomy_releases.id", ondelete="RESTRICT"), nullable=False
    )
    published_paper_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("exam_papers.id", ondelete="RESTRICT"), nullable=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    batch_code: Mapped[str] = mapped_column(String(50), nullable=False, default="default", server_default="default")
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    exam_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="uploaded", server_default="uploaded")
    parser_version: Mapped[str] = mapped_column(String(50), nullable=False, default="vision-v1", server_default="vision-v1")
    expected_question_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    validation_summary: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class QuestionSourceDocument(TimestampMixin, Base):
    __tablename__ = "question_source_documents"
    __table_args__ = (
        CheckConstraint("role IN ('combined', 'questions', 'answers')", name="ck_source_document_role"),
        CheckConstraint("status IN ('uploaded', 'rendered', 'failed')", name="ck_source_document_status"),
        CheckConstraint("file_size > 0", name="ck_source_document_file_size"),
        CheckConstraint("page_count IS NULL OR page_count > 0", name="ck_source_document_page_count"),
        UniqueConstraint("batch_id", "sha256", name="uq_source_document_batch_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("question_import_batches.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="combined", server_default="combined")
    original_name: Mapped[str] = mapped_column(String(300), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_selections: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    section_suggestion: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    extracted_content: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploaded", server_default="uploaded")
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class QuestionSourcePageExtraction(TimestampMixin, Base):
    __tablename__ = "question_source_page_extractions"
    __table_args__ = (
        CheckConstraint("role IN ('questions', 'answers')", name="ck_page_extraction_role"),
        CheckConstraint("status IN ('pending', 'completed', 'failed')", name="ck_page_extraction_status"),
        CheckConstraint("page_no > 0", name="ck_page_extraction_page_no"),
        UniqueConstraint(
            "document_id", "page_no", "role", "prompt_version", name="uq_page_extraction_version"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("question_source_documents.id", ondelete="CASCADE"), nullable=False
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class QuestionImportRepairRun(TimestampMixin, Base):
    __tablename__ = "question_import_repair_runs"
    __table_args__ = (
        CheckConstraint("role IN ('questions', 'answers')", name="ck_import_repair_role"),
        CheckConstraint("attempt > 0 AND attempt <= 2", name="ck_import_repair_attempt"),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_import_repair_status",
        ),
        Index("ix_import_repair_batch_status", "batch_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("question_import_batches.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("question_source_documents.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    page_numbers: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    question_numbers: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class QuestionImportGroup(TimestampMixin, Base):
    __tablename__ = "question_import_groups"
    __table_args__ = (
        UniqueConstraint("batch_id", "source_label", name="uq_import_group_batch_label"),
        CheckConstraint("sort_order >= 0", name="ck_import_group_sort_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("question_import_batches.id", ondelete="CASCADE"), nullable=False
    )
    source_label: Mapped[str] = mapped_column(String(50), nullable=False)
    explanation_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    material_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    source_refs: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class QuestionImportItem(TimestampMixin, Base):
    __tablename__ = "question_import_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "question_no", name="uq_import_item_batch_number"),
        CheckConstraint("question_no > 0", name="ck_import_item_question_no"),
        CheckConstraint("score >= 0", name="ck_import_item_score"),
        CheckConstraint("question_type IN ('single_choice', 'multiple_choice')", name="ck_import_item_type"),
        CheckConstraint(
            "status IN ('needs_review', 'approved', 'rejected', 'blocked', 'published')",
            name="ck_import_item_status",
        ),
        Index("ix_import_item_batch_status", "batch_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("question_import_batches.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("question_import_groups.id", ondelete="SET NULL"), nullable=True
    )
    published_question_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("questions.id", ondelete="RESTRICT"), nullable=True
    )
    question_no: Mapped[int] = mapped_column(Integer, nullable=False)
    question_type: Mapped[str] = mapped_column(String(30), nullable=False, default="single_choice", server_default="single_choice")
    stem_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    options_payload: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    correct_option_keys: Mapped[list[str]] = mapped_column(
        ARRAY(String(10)), nullable=False, default=list, server_default=text("'{}'::varchar[]")
    )
    explanation_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=1, server_default="1")
    source_refs: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    validation_issues: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="needs_review", server_default="needs_review")
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class QuestionAsset(TimestampMixin, Base):
    __tablename__ = "question_assets"
    __table_args__ = (
        CheckConstraint("page_no > 0", name="ck_question_asset_page_no"),
        CheckConstraint("width > 0 AND height > 0", name="ck_question_asset_dimensions"),
        CheckConstraint("kind IN ('figure', 'table', 'code', 'source_snapshot')", name="ck_question_asset_kind"),
        UniqueConstraint("document_id", "page_no", "sha256", name="uq_question_asset_source_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("question_source_documents.id", ondelete="RESTRICT"), nullable=False
    )
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False, default="image/png", server_default="image/png")
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)


class PracticeQuestion(TimestampMixin, Base):
    """One independently presented, reviewed and submitted practice unit."""
    __tablename__ = "practice_questions"
    content_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    __table_args__ = (
        UniqueConstraint("paper_id", "source_label", name="uq_practice_paper_label"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("exam_papers.id", ondelete="RESTRICT"))
    group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("question_groups.id", ondelete="RESTRICT"))
    source_label: Mapped[str] = mapped_column(String(50))
    content_hash: Mapped[str] = mapped_column(String(64))


class PracticeQuestionPart(Base):
    __tablename__ = "practice_question_parts"
    __table_args__ = (
        UniqueConstraint("practice_question_id", "position", name="uq_practice_part_position"),
        CheckConstraint("position > 0", name="ck_practice_part_position"),
    )
    practice_question_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("practice_questions.id", ondelete="CASCADE"), primary_key=True)
    question_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("questions.id", ondelete="RESTRICT"), primary_key=True, unique=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class PracticeQuestionRevision(Base):
    __tablename__ = "practice_question_revisions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    practice_question_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("practice_questions.id", ondelete="RESTRICT"))
    batch_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("question_import_batches.id", ondelete="RESTRICT"))
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuestionAssetUsage(TimestampMixin, Base):
    __tablename__ = "question_asset_usages"
    __table_args__ = (
        CheckConstraint(
            "(CASE WHEN import_item_id IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN import_group_id IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN question_id IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN question_group_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_question_asset_usage_one_owner",
        ),
        CheckConstraint(
            "placement IN ('stem', 'group_material', 'option', 'explanation')",
            name="ck_question_asset_usage_placement",
        ),
        CheckConstraint("sort_order >= 0", name="ck_question_asset_usage_sort_order"),
        Index("ix_asset_usage_import_item", "import_item_id"),
        Index("ix_asset_usage_import_group", "import_group_id"),
        Index("ix_asset_usage_question", "question_id"),
        Index("ix_asset_usage_question_group", "question_group_id"),
        Index("ix_asset_usage_asset", "asset_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("question_assets.id", ondelete="RESTRICT"), nullable=False
    )
    import_item_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("question_import_items.id", ondelete="CASCADE"), nullable=True
    )
    import_group_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("question_import_groups.id", ondelete="CASCADE"), nullable=True
    )
    question_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("questions.id", ondelete="CASCADE"), nullable=True
    )
    question_group_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("question_groups.id", ondelete="CASCADE"), nullable=True
    )
    placement: Mapped[str] = mapped_column(String(30), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    alt_text: Mapped[str] = mapped_column(
        String(300), nullable=False, default="原题素材", server_default="原题素材"
    )


class QuestionClassificationRun(TimestampMixin, Base):
    __tablename__ = "question_classification_runs"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'completed', 'failed')", name="ck_classification_run_status"),
        Index("ix_classification_run_item_status", "import_item_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    import_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("question_import_items.id", ondelete="CASCADE"), nullable=False
    )
    taxonomy_release_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_taxonomy_releases.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    catalog_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class QuestionClassificationCandidate(Base):
    __tablename__ = "question_classification_candidates"
    __table_args__ = (
        CheckConstraint("role IN ('primary', 'related')", name="ck_classification_candidate_role"),
        CheckConstraint("rank > 0", name="ck_classification_candidate_rank"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_classification_candidate_confidence"),
        UniqueConstraint("run_id", "knowledge_node_id", "role", name="uq_classification_candidate"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("question_classification_runs.id", ondelete="CASCADE"), nullable=False
    )
    knowledge_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


class QuestionImportKnowledgeSelection(TimestampMixin, Base):
    __tablename__ = "question_import_knowledge_selections"
    __table_args__ = (
        CheckConstraint("role IN ('primary', 'related')", name="ck_import_selection_role"),
        CheckConstraint("source IN ('manual', 'ai')", name="ck_import_selection_source"),
        UniqueConstraint("import_item_id", "knowledge_node_id", "role", name="uq_import_selection_node_role"),
        Index(
            "uq_import_selection_primary",
            "import_item_id",
            unique=True,
            postgresql_where=text("role = 'primary'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    import_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("question_import_items.id", ondelete="CASCADE"), nullable=False
    )
    knowledge_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("question_classification_candidates.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)

# Register platform models in the single Alembic metadata registry.
from app.core.models import User, Role, UserRole, AuditEvent, Job, Outbox, Attempt, AttemptPart, AttemptAsset  # noqa: E402,F401
