"""Durable job orchestration. PostgreSQL owns execution state; Celery carries IDs."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select, text
from app.core.errors import fail
from app.models import Job, Outbox

KINDS = {"parse", "classify", "assist", "archive_images", "supplement"}


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


async def enqueue(
    session, *, user_id, batch, kind, payload, key, scope=None, request_id="worker"
):
    if kind not in KINDS:
        raise fail(422, "JOB_KIND", "任务类型无效")
    scope = scope or f"batch:{batch.id}:{kind}"
    digest = fingerprint(payload)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": f"job:{user_id}:{scope}:{key}"},
    )
    old = await session.scalar(
        select(Job).where(
            Job.user_id == user_id, Job.scope == scope, Job.idempotency_key == key
        )
    )
    if old:
        if old.input_hash != digest:
            raise fail(409, "IDEMPOTENCY_CONFLICT", "此标识已用于不同任务")
        return old
    job = Job(
        user_id=user_id,
        batch_id=batch.id,
        kind=kind,
        scope=scope,
        payload=payload,
        idempotency_key=key,
        input_hash=digest,
        request_id=request_id,
    )
    session.add(job)
    await session.flush()
    session.add(Outbox(job_id=job.id))
    return job


def read_job(job):
    return dict(
        job_id=str(job.id),
        batch_id=str(job.batch_id),
        kind=job.kind,
        status=job.status,
        progress=100 if job.status == "succeeded" else 0,
        result=job.result,
        error_code=job.error_code,
        error_detail=job.error_detail,
        status_url=f"/api/v2/admin/jobs/{job.id}",
    )
