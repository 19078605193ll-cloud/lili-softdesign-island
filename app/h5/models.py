import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.models import Base, TimestampMixin


class Preference(TimestampMixin, Base):
    __tablename__ = "user_learning_preferences"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("exam_subjects.id"))
    motto: Mapped[str] = mapped_column(String(200), default="千里之行，始于足下。")


class ExamPlan(TimestampMixin, Base):
    __tablename__ = "user_exam_plans"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_subjects.id"), primary_key=True
    )
    title: Mapped[str] = mapped_column(String(100))
    exam_date: Mapped[date] = mapped_column(Date)
    start_date: Mapped[date] = mapped_column(Date)


class LearningSession(TimestampMixin, Base):
    __tablename__ = "learning_sessions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    subject_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exam_subjects.id"))
    source: Mapped[str] = mapped_column(String(30))
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(200))
    question_ids: Mapped[list] = mapped_column(JSONB)
    position: Mapped[int] = mapped_column(Integer, default=0)
    drafts: Mapped[dict] = mapped_column(JSONB, default=dict)
    attempts: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (Index("ix_learning_session_resume", "user_id", "subject_id", "source", "source_id"),)


class RelatedPractice(TimestampMixin, Base):
    __tablename__ = "related_practice_requests"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("learning_sessions.id"))
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("practice_questions.id"))
    request_key: Mapped[str] = mapped_column(String(200))
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        UniqueConstraint("user_id", "request_key", name="uq_related_request_key"),
        Index("ix_related_resume", "user_id", "session_id", "question_id"),
    )


class Mark(TimestampMixin, Base):
    __tablename__ = "user_question_marks"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("practice_questions.id"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    active: Mapped[bool] = mapped_column(default=True)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("learning_attempts.id")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_mark_user_active_kind", "user_id", "active", "kind"),)


class TutorSession(TimestampMixin, Base):
    __tablename__ = "tutor_sessions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("practice_questions.id"))
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("learning_attempts.id")
    )
    context: Mapped[dict] = mapped_column(JSONB)
    prompt_version: Mapped[str] = mapped_column(String(64))
    system_prompt: Mapped[str] = mapped_column(Text)


class TutorMessage(TimestampMixin, Base):
    __tablename__ = "tutor_messages"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tutor_sessions.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("teaching_variants.id")
    )
    __table_args__ = (UniqueConstraint("job_id", "role", name="uq_tutor_job_role"),)


class Variant(TimestampMixin, Base):
    __tablename__ = "teaching_variants"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exam_subjects.id"))
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_nodes.id"))
    match_key: Mapped[str] = mapped_column(String(64), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="approved")
    content: Mapped[dict] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(64))
    validation: Mapped[dict] = mapped_column(JSONB)


class VariantAnswer(TimestampMixin, Base):
    __tablename__ = "teaching_variant_answers"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teaching_variants.id"), primary_key=True
    )
    answer: Mapped[str] = mapped_column(String(10))
    correct: Mapped[bool]


class VariantReport(TimestampMixin, Base):
    __tablename__ = "teaching_variant_reports"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teaching_variants.id"))
    reason: Mapped[str] = mapped_column(String(1000))
