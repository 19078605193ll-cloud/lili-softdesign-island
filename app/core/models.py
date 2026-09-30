"""Identity, audit, durable execution and learning records; one metadata registry."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    avatar_key: Mapped[str | None] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(default=True, server_default="true")
    auth_version: Mapped[int] = mapped_column(default=1, server_default="1")


class Role(Base):
    __tablename__ = "roles"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(
        ForeignKey("roles.name", ondelete="RESTRICT"), primary_key=True
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(100))
    object_id: Mapped[str] = mapped_column(String(200))
    request_id: Mapped[str] = mapped_column(String(64))
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (Index("ix_audit_created", "created_at"),)


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "scope", "idempotency_key", name="uq_job_idempotency"
        ),
        CheckConstraint(
            "status IN ('queued','running','retry_wait','succeeded','failed','superseded','cancelled')",
            name="ck_job_status",
        ),
        Index("ix_job_dispatch", "status", "available_at"),
        Index("ix_job_batch", "batch_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("question_import_batches.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(40))
    scope: Mapped[str] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    input_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(
        String(20), default="queued", server_default="queued"
    )
    generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    retries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(String(64), default="worker")


class Outbox(Base):
    __tablename__ = "task_outbox"
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Attempt(Base):
    __tablename__ = "learning_attempts"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_attempt_idempotency"),
        Index("ix_attempt_user_created", "user_id", "created_at", "id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    practice_question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("practice_questions.id", ondelete="RESTRICT")
    )
    content_version: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    answers: Mapped[dict] = mapped_column(JSONB)
    score: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AttemptPart(Base):
    __tablename__ = "learning_attempt_parts"
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("learning_attempts.id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="RESTRICT"), primary_key=True
    )
    answer: Mapped[str] = mapped_column(String(10))
    correct: Mapped[bool]
    score: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    knowledge: Mapped[list] = mapped_column(JSONB)


class AttemptAsset(Base):
    __tablename__ = "learning_attempt_assets"
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("learning_attempts.id", ondelete="CASCADE"), primary_key=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_assets.id", ondelete="RESTRICT"), primary_key=True
    )
