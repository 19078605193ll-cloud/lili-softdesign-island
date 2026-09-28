from datetime import datetime, timedelta, timezone
import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Job, Outbox, QuestionImportBatch, QuestionClassificationRun
from app.infrastructure import worker
from tests.test_markdown_integration import upload
from tests.test_markdown_integration import storage as storage

pytestmark = pytest.mark.integration


async def create(client, batch, kind="classify", **kwargs):
    state = (await client.get("/api/v1/admin/markdown-batches/" + batch)).json()
    response = await client.post(
        "/api/v2/admin/import-batches/" + batch + "/jobs",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json=dict(kind=kind, revision=state["revision"], **kwargs),
    )
    assert response.status_code == 202, response.text
    return uuid.UUID(response.json()["job_id"])


async def test_retry_budget_and_terminal_failure(client, session, storage, monkeypatch):
    batch = await upload(client)
    jid = await create(client, batch)

    async def broken(*args):
        raise httpx.ConnectError("secret-provider-url-must-not-leak")

    monkeypatch.setattr(worker, "perform", broken)
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    for attempt in range(4):
        await session.commit()
        await worker.execute(jid, factory)
        async with factory.begin() as db:
            job = await db.get(Job, jid)
            assert job.status == ("retry_wait" if attempt < 3 else "failed")
            assert "secret-provider" not in job.error_detail
            job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.expire_all()
    response = await client.post("/api/v2/admin/jobs/" + str(jid) + "/retry")
    assert response.status_code == 202, response.text


async def test_external_call_holds_no_transaction_and_late_result_is_ignored(
    client, session, storage, monkeypatch
):
    batch = await upload(client)
    jid = await create(client, batch)
    factory = async_sessionmaker(session.bind, expire_on_commit=False)

    async def edit_during_call(task, data):
        # A separate writer can acquire the batch lock while provider work runs.
        from sqlalchemy import text

        async with factory.begin() as db:
            await db.execute(text("SET LOCAL lock_timeout='500ms'"))
            row = await db.scalar(
                select(QuestionImportBatch)
                .where(QuestionImportBatch.id == uuid.UUID(batch))
                .with_for_update()
            )
            row.revision += 1
        return {"empty": True}

    monkeypatch.setattr(worker, "perform", edit_during_call)
    await session.commit()
    await worker.execute(jid, factory)
    async with factory() as db:
        assert (await db.get(Job, jid)).status == "superseded"
        assert not list(await db.scalars(select(QuestionClassificationRun)))


async def test_dispatch_failure_preserves_outbox(client, session, storage):
    from app.infrastructure.dispatcher import dispatch_once

    batch = await upload(client)
    jid = await create(client, batch)
    await session.commit()
    factory = async_sessionmaker(session.bind, expire_on_commit=False)

    def unavailable(_):
        raise ConnectionError("broker unavailable")

    with pytest.raises(ConnectionError):
        await dispatch_once(factory, unavailable)
    async with factory() as db:
        assert (await db.get(Outbox, jid)).sent_at is None
        assert (await db.get(Job, jid)).status == "queued"
    sent = []
    assert await dispatch_once(factory, sent.append) >= 1
    assert str(jid) in sent


async def test_retry_rejects_changed_input(client, session, storage):
    batch = await upload(client)
    jid = await create(client, batch)
    job = await session.get(Job, jid)
    job.status = "failed"
    row = await session.get(QuestionImportBatch, uuid.UUID(batch))
    row.revision += 1
    await session.commit()
    assert (
        await client.post("/api/v2/admin/jobs/" + str(jid) + "/retry")
    ).status_code == 409
