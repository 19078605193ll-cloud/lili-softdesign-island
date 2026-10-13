import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin


class Recall(TimestampMixin, Base):
    __tablename__ = "recalls"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exam_subjects.id"))
    slug: Mapped[str] = mapped_column(String(160))
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    active: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (
        UniqueConstraint("subject_id", "slug"),
        ForeignKeyConstraint(
            ["published_version_id", "id"],
            ["recall_versions.id", "recall_versions.recall_id"],
            name="fk_recall_published_version",
            use_alter=True,
        ),
    )


class RecallVersion(TimestampMixin, Base):
    __tablename__ = "recall_versions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    recall_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recalls.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(20))
    schema_version: Mapped[int] = mapped_column(default=1)
    content: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    revision: Mapped[int] = mapped_column(default=1)
    content_hash: Mapped[str] = mapped_column(String(64))
    reviewed_hash: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(20), default="manual")
    provenance: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (
        UniqueConstraint("recall_id", "number"),
        UniqueConstraint("id", "recall_id"),
        CheckConstraint("type IN ('table','sentence','relation')"),
        CheckConstraint("status IN ('draft','pending','approved','published')"),
        CheckConstraint("source IN ('manual','ai')"),
    )


class RecallKnowledgeLink(Base):
    __tablename__ = "recall_knowledge_links"
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recall_versions.id"), primary_key=True
    )
    knowledge_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_nodes.id"), primary_key=True, index=True
    )


class RecallBlank(Base):
    __tablename__ = "recall_blanks"
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recall_versions.id"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(200))
    answer: Mapped[str] = mapped_column(Text)
    acceptable_answers: Mapped[list] = mapped_column(JSONB)
    normalization: Mapped[dict] = mapped_column(JSONB)
    hints: Mapped[list] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("version_id", "position"),)


class RecallSession(TimestampMixin, Base):
    __tablename__ = "recall_sessions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    recall_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recalls.id"))
    version_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    phase: Mapped[str] = mapped_column(String(20), default="first")
    slot_presentations: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    queue: Mapped[list] = mapped_column(JSONB)
    position: Mapped[int] = mapped_column(default=0)
    revision: Mapped[int] = mapped_column(default=1)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "recall_id"],
            ["recall_versions.id", "recall_versions.recall_id"],
        ),
        UniqueConstraint("id", "version_id"),
        CheckConstraint("phase IN ('first','retest_ready','retest','completed')"),
        Index(
            "uq_recall_active_session",
            "user_id",
            "recall_id",
            unique=True,
            postgresql_where=text("phase <> 'completed'"),
        ),
        Index("ix_recall_session_recent", "user_id", "updated_at"),
    )


class RecallSessionBlank(Base):
    __tablename__ = "recall_session_blanks"
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    blank_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    version_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    draft: Mapped[str] = mapped_column(Text, default="")
    round_error_count: Mapped[int] = mapped_column(default=0)
    pass_error_count: Mapped[int] = mapped_column(default=0)
    needs_retest: Mapped[bool] = mapped_column(default=False)
    mastered: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "version_id"],
            ["recall_sessions.id", "recall_sessions.version_id"],
        ),
        ForeignKeyConstraint(
            ["version_id", "blank_key"],
            ["recall_blanks.version_id", "recall_blanks.key"],
        ),
        CheckConstraint("round_error_count >= 0 AND pass_error_count BETWEEN 0 AND 3"),
    )


class RecallSubmission(Base):
    __tablename__ = "recall_submissions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    recall_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recalls.id"))
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    blank_key: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    answer: Mapped[str] = mapped_column(Text)
    normalized: Mapped[str] = mapped_column(Text)
    correct: Mapped[bool]
    hint_level: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "blank_key"],
            ["recall_session_blanks.session_id", "recall_session_blanks.blank_key"],
        ),
        UniqueConstraint("user_id", "idempotency_key"),
        Index("ix_recall_errors", "user_id", "recall_id", "blank_key", "created_at"),
    )
