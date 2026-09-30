"""Deterministic worker execution for API tests; no Celery eager-mode shortcuts in production."""

import uuid
import asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.infrastructure.worker import execute


async def drain(client, job_id, session, storage):
    from app.config import get_settings

    previous = get_settings().import_storage_root
    get_settings().import_storage_root = str(storage.root)
    try:
        # Release fixture's read transaction before a separate worker writes.
        await session.commit()
        # A committed queued job may not yet be due when the host/VM clock adjusts.
        # Match dispatcher behavior instead of assuming one immediate delivery claims it.
        for _ in range(40):
            await execute(job_id, async_sessionmaker(session.bind, expire_on_commit=False))
            session.expire_all()
            job = (await client.get("/api/v2/admin/jobs/" + str(job_id))).json()
            if job['status'] != 'queued':
                break
            await session.commit()
            await asyncio.sleep(.05)
        if job["status"] == "succeeded":
            for child in job.get("result", {}).get("children", []):
                await drain(client, child, session, storage)
        return job
    finally:
        get_settings().import_storage_root = previous


async def start(client, session, storage, batch_id, kind, **kwargs):
    state = (await client.get("/api/v1/admin/markdown-batches/" + str(batch_id))).json()
    response = await client.post(
        f"/api/v2/admin/import-batches/{batch_id}/jobs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json=dict(kind=kind, revision=state["revision"], **kwargs),
    )
    assert response.status_code == 202, response.text
    return await drain(client, response.json()["job_id"], session, storage)
