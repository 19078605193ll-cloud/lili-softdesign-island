"""Recoverable outbox dispatcher; duplicate dispatch is expected and fenced by workers."""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.config import get_settings
from app.core.runtime import run_async
from app.infrastructure.celery_app import celery_app
from app.models import Job, Outbox


async def dispatch_once(factory, send=None):
    if not get_settings().tasks_enabled or not get_settings().writes_enabled:
        return 0
    async with factory.begin() as session:
        # Leases and availability are database timestamps: use the same clock.
        now = await session.scalar(select(func.clock_timestamp()))
        expired = await session.scalars(
            select(Job)
            .where(Job.status == "running", Job.lease_until < now)
            .with_for_update(skip_locked=True)
        )
        for job in expired:
            job.generation += 1
            job.retries += 1
            maximum = 1 if job.kind in {'tutor','variant','variant_review','variant_wait'} else 3
            job.status = "queued" if job.retries <= maximum else "failed"
            job.error_code = "WORKER_LOST"
            job.error_detail = (
                "任务进程中断，请重试" if job.status == "failed" else None
            )
            (await session.get(Outbox, job.id)).sent_at = None
        rows = (
            await session.execute(
                select(Job, Outbox)
                .join(Outbox)
                .where(
                    Job.status.in_(["queued", "retry_wait"]),
                    Job.available_at <= now,
                    or_(
                        Outbox.sent_at.is_(None),
                        Outbox.sent_at < now - timedelta(seconds=60),
                    ),
                )
                .limit(50)
                .with_for_update(skip_locked=True)
            )
        ).all()
        sent = 0
        for job, outbox in rows:
            # Publication is not a business transaction; rollback simply permits re-dispatch.
            if send:
                await asyncio.to_thread(send, str(job.id))
            else:
                await asyncio.to_thread(celery_app.send_task, 'island.execute', args=[str(job.id)],
                    time_limit=600 if job.kind in {'tutor','variant','variant_review'} else 240)
            outbox.sent_at = now
            sent += 1
        return sent


async def main():
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        while True:
            try:
                await dispatch_once(factory)
            except Exception as exc:
                import logging

                logging.getLogger(__name__).error(
                    "dispatch_failed",
                    extra={"fields": {"exception_type": type(exc).__name__}},
                )
            await asyncio.sleep(2)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    run_async(main())
