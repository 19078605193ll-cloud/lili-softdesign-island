from fastapi import APIRouter, Request
from sqlalchemy import select, text, func
from app.core.errors import fail
from app.core.security import redis_client, require
from app.db import SessionFactory
from app.models import AuditEvent, Job, Outbox

router = APIRouter()


@router.get("/health/live")
async def live():
    return {"status": "ok"}


@router.get("/health/ready")
async def ready():
    try:
        async with SessionFactory() as session:
            revision = await session.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            if revision != "20260927_0013":
                raise ValueError("schema version")
        async with redis_client() as redis:
            await redis.ping()
    except Exception as exc:
        raise fail(503, "NOT_READY", "依赖服务尚未就绪") from exc
    return {"status": "ready"}


@router.get("/api/v1/admin/audit")
async def audit(request: Request):
    await require(request, "audit:read")
    async with SessionFactory() as session:
        rows = await session.scalars(
            select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(100)
        )
        return [
            {
                "id": str(r.id),
                "user_id": str(r.user_id) if r.user_id else None,
                "action": r.action,
                "object_id": r.object_id,
                "request_id": r.request_id,
                "summary": r.summary,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]


@router.get("/internal/metrics")
async def metrics(request: Request):
    await require(request, "audit:read")
    async with SessionFactory() as session:
        states = (
            await session.execute(select(Job.status, func.count()).group_by(Job.status))
        ).all()
        oldest = await session.scalar(
            select(func.min(Job.created_at)).where(
                Job.status.in_(["queued", "retry_wait"])
            )
        )
        expired = await session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.status == "running", Job.lease_until < func.now())
        )
        pending = await session.scalar(
            select(func.count()).select_from(Outbox).where(Outbox.sent_at.is_(None))
        )
    return {
        "jobs": dict(states),
        "oldest_queued": oldest,
        "expired_leases": expired,
        "pending_outbox": pending,
    }
