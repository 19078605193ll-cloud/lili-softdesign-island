from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text

from app.config import get_settings
from app.core.errors import fail
from app.core.security import require
from app.imports.dependencies import SessionDependency, StorageDependency
from app.imports.service import create_batch_record
from app.infrastructure.jobs import enqueue, read_job
from app.models import Job, Outbox, QuestionImportBatch, QuestionSourceDocument

router = APIRouter(prefix="/api/v2/admin", tags=["durable-imports"])
Key = Annotated[str, Header(min_length=1, max_length=200)]


def enabled():
    if not get_settings().tasks_enabled:
        raise fail(503, "TASKS_DISABLED", "后台任务已暂停")


@router.post("/import-batches", status_code=202)
async def upload(
    request: Request,
    session: SessionDependency,
    storage: StorageDependency,
    idempotency_key: Key,
    file: Annotated[UploadFile, File()],
    year: Annotated[int, Form(ge=2000, le=2100)],
    period: Annotated[str, Form(pattern="^(first_half|second_half|other)$")],
    title: Annotated[str, Form(min_length=1, max_length=300)],
    batch_code: Annotated[str, Form(min_length=1, max_length=50)] = "default",
    subject_code: Annotated[str, Form()] = "software-designer.foundation",
    taxonomy_version: Annotated[str | None, Form()] = None,
):
    enabled()
    user = await require(request, "imports:edit")
    try:
        data = await file.read(storage.max_bytes + 1)
    finally:
        await file.close()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".md", ".zip"} or not data or len(data) > storage.max_bytes:
        raise fail(422, "UPLOAD_INVALID", "请上传大小符合限制的 Markdown 或 ZIP")
    incoming = dict(
        year=year,
        period=period,
        title=title,
        batch_code=batch_code,
        subject_code=subject_code,
        taxonomy_version=taxonomy_version,
        sha256=hashlib.sha256(data).hexdigest(),
        suffix=suffix,
    )
    # Serialize retries before allocating a new batch/directory.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": f"upload:{user.id}:{idempotency_key}"},
    )
    old = await session.scalar(
        select(Job).where(
            Job.user_id == user.id,
            Job.scope == "upload",
            Job.idempotency_key == idempotency_key,
        )
    )
    if old:
        if old.payload.get("upload") != incoming:
            raise fail(409, "IDEMPOTENCY_CONFLICT", "此上传标识已用于不同文件")
        return read_job(old)
    batch = await create_batch_record(
        session,
        subject_code=subject_code,
        taxonomy_version=taxonomy_version,
        year=year,
        period=period,
        batch_code=batch_code,
        title=title,
        exam_date=None,
        source_reference="考生回忆版",
    )
    batch.parser_version = "markdown-v3"
    batch.expected_question_count = None
    staging = storage.resolve(f".staging/{batch.id}")
    target = storage.resolve(str(batch.id))

    def save():
        staging.mkdir(parents=True, exist_ok=False)
        (staging / ("upload" + suffix)).write_bytes(data)
        staging.rename(target)

    await asyncio.to_thread(save)
    job = await enqueue(
        session,
        user_id=user.id,
        batch=batch,
        kind="parse",
        key=idempotency_key,
        scope="upload",
        payload={
            "upload": incoming,
            "source": f"{batch.id}/upload{suffix}",
            "filename": "upload" + suffix,
        },
        request_id=request.state.request_id,
    )
    output = read_job(job)
    # Never delete a promoted directory after commit was attempted; reconcile it later.
    await session.commit()
    return output


class CreateJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["classify", "assist", "archive_images", "supplement"]
    block_id: str | None = None
    question_no: int | None = None
    revision: int
    force: bool = False


@router.post("/import-batches/{batch_id}/jobs", status_code=202)
async def create_job(
    batch_id: uuid.UUID,
    payload: CreateJob,
    request: Request,
    session: SessionDependency,
    idempotency_key: Key,
):
    enabled()
    user = await require(request, "imports:edit")
    batch = await session.scalar(
        select(QuestionImportBatch)
        .where(QuestionImportBatch.id == batch_id)
        .with_for_update()
    )
    if not batch:
        raise fail(404, "BATCH_NOT_FOUND", "批次不存在")
    # Idempotent replay remains valid even after the work changes batch state.
    old = await session.scalar(
        select(Job).where(
            Job.user_id == user.id,
            Job.scope == f"batch:{batch.id}:{payload.kind}",
            Job.idempotency_key == idempotency_key,
        )
    )
    values = payload.model_dump()
    if old:
        if old.payload.get("request") != values:
            raise fail(409, "IDEMPOTENCY_CONFLICT", "任务标识已使用")
        return read_job(old)
    if batch.revision != payload.revision:
        raise fail(409, "CONTENT_CHANGED", "批次已更新，请刷新")
    if (batch.status == "published") != (payload.kind == "supplement"):
        raise fail(409, "BATCH_STATE", "当前批次状态不允许此操作")
    doc = await session.scalar(
        select(QuestionSourceDocument).where(
            QuestionSourceDocument.batch_id == batch_id
        )
    )
    if not doc:
        raise fail(409, "PARSE_PENDING", "原文尚未解析完成")
    blocks = doc.extracted_content.get("blocks", [])
    if payload.block_id:
        block = next((b for b in blocks if b["id"] == payload.block_id), None)
        if not block:
            raise fail(404, "BLOCK_NOT_FOUND", "题块不存在")
        if block.get("confirmed") or block.get("excluded"):
            raise fail(409, "BLOCK_REVIEWED", "已审核或排除题块不能重新处理")
    if payload.question_no is not None and not payload.block_id:
        raise fail(422, "BLOCK_REQUIRED", "按小问分类必须指定题块")
    if payload.question_no is not None and (
        payload.kind != "classify"
        or payload.question_no not in {p["question_no"] for p in block.get("parts", [])}
    ):
        raise fail(422, "QUESTION_SCOPE", "小问不属于此分类目标")
    if payload.block_id and payload.kind not in {"classify", "assist"}:
        raise fail(422, "JOB_SCOPE", "此任务不支持题块范围")
    if payload.kind == "assist" and not payload.block_id:
        raise fail(422, "BLOCK_REQUIRED", "文本辅助必须指定题块")
    job = await enqueue(
        session,
        user_id=user.id,
        batch=batch,
        kind=payload.kind,
        payload={"request": values, "revision": batch.revision},
        key=idempotency_key,
        request_id=request.state.request_id,
    )
    output = read_job(job)
    await session.commit()
    return output


@router.get("/jobs/{job_id}")
async def get_job(job_id: uuid.UUID, request: Request, session: SessionDependency):
    await require(request, "imports:read")
    job = await session.get(Job, job_id)
    if not job:
        raise fail(404, "JOB_NOT_FOUND", "任务不存在")
    return read_job(job)


@router.get("/import-batches/{batch_id}/jobs")
async def batch_jobs(batch_id: uuid.UUID, request: Request, session: SessionDependency):
    await require(request, "imports:read")
    rows = await session.scalars(
        select(Job)
        .where(Job.batch_id == batch_id)
        .order_by(Job.created_at.desc())
        .limit(100)
    )
    return {"items": [read_job(j) for j in rows]}


@router.post("/jobs/{job_id}/retry", status_code=202)
async def retry(job_id: uuid.UUID, request: Request, session: SessionDependency):
    enabled()
    await require(request, "imports:edit")
    job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job:
        raise fail(404, "JOB_NOT_FOUND", "任务不存在")
    batch = await session.get(QuestionImportBatch, job.batch_id)
    if job.status != "failed" or (
        "revision" in job.payload and batch.revision != job.payload["revision"]
    ):
        raise fail(409, "RETRY_NOT_ALLOWED", "任务已过期或不可重试，请重新提交")
    job.status = "queued"
    job.retries = 0
    job.error_code = job.error_detail = None
    from datetime import datetime, timezone

    job.available_at = datetime.now(timezone.utc)
    outbox = await session.get(Outbox, job.id)
    outbox.sent_at = None
    output = read_job(job)
    await session.commit()
    return output
