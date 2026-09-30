import uuid
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.models import ExamPaper, QuestionImportBatch, Attempt, AuditEvent, Job
from tests.test_h5 import setup_questions, answer
from tests.test_markdown_integration import upload, review, storage as storage

pytestmark = pytest.mark.integration


async def test_title_delete_restore_preserves_history(
    client, session, storage, monkeypatch
):
    subject, paper_id, units = await setup_questions(client, session)
    batch = await session.scalar(
        select(QuestionImportBatch).where(
            QuestionImportBatch.published_paper_id == uuid.UUID(paper_id)
        )
    )
    bid = str(batch.id)
    url = "/api/v1/admin/import-batches/" + bid
    practice = (
        await client.post(
            "/api/v2/learning/sessions",
            json={
                "subject_id": subject,
                "source": "paper",
                "source_id": paper_id,
                "mode": "resume",
            },
        )
    ).json()
    q = units[0]
    attempt = await answer(client, q, ["C"] * len(q["parts"]), practice["id"])
    original_snapshot = (await session.get(Attempt, uuid.UUID(attempt["id"]))).snapshot
    title = "新标题" * 100
    renamed = await client.patch(url + "/title", json={"title": "  " + title + "  "})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == title
    papers = (await client.get("/api/v2/practice/papers?subject_id=" + subject)).json()[
        "items"
    ]
    assert papers[0]["title"] == title
    assert (await client.get("/api/v2/learning/sessions/" + practice["id"])).json()[
        "title"
    ] == title
    dashboard = (
        await client.get("/api/v2/learning/dashboard?subject_id=" + subject)
    ).json()
    assert dashboard["recent"][0]["title"] == title
    resumed = await client.post(
        "/api/v2/learning/sessions",
        json={
            "subject_id": subject,
            "source": "paper",
            "source_id": paper_id,
            "mode": "resume",
        },
    )
    assert resumed.status_code == 201, resumed.text
    for value in (" ", "a" * 301):
        assert (
            await client.patch(url + "/title", json={"title": value})
        ).status_code == 422
    stale = await client.patch(
        url + "/title", json={"title": "stale"}, headers={"If-Match": "0"}
    )
    assert stale.status_code == 409
    cleanup = Mock(side_effect=AssertionError("Published assets must not be deleted"))
    monkeypatch.setattr(storage, "remove_batch", cleanup)
    deleted = await client.delete(url)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["soft_deleted"] is True
    assert (await client.delete(url)).status_code == 200
    cleanup.assert_not_called()
    assert title not in (await client.get("/admin/imports")).text
    assert title in (await client.get("/admin/imports?view=deleted")).text
    assert (await client.get("/api/v2/practice/papers?subject_id=" + subject)).json()[
        "items"
    ] == []
    state = (await client.get("/api/v2/learning/sessions/" + practice["id"])).json()
    assert state["read_only"] and state["attempts"][q["id"]] == attempt["id"]
    assert (
        await client.get("/api/v2/learning/attempts/" + attempt["id"])
    ).status_code == 200
    other = units[1]
    rejected = await client.post(
        "/api/v2/learning/attempts",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json={
            "practice_question_id": other["id"],
            "content_version": other["content_version"],
            "answers": {p["id"]: "A" for p in other["parts"]},
            "session_id": practice["id"],
        },
    )
    assert rejected.status_code == 409 and "已删除" in rejected.text
    related = await client.post(
        f"/api/v2/practice/questions/{q['id']}/related-practice",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json={"session_id": practice["id"]},
    )
    assert related.status_code == 409
    ai = await client.post(
        "/api/v2/learning/tutor/sessions",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json={"question_id": q["id"], "attempt_id": attempt["id"]},
    )
    assert ai.status_code == 409 and "已删除" in ai.text
    publish = await client.post("/api/v1/admin/markdown-batches/" + bid + "/publish")
    assert publish.status_code == 409
    assert (await client.post(url + "/restore")).status_code == 200
    assert (await client.post(url + "/restore")).status_code == 200
    assert title in (await client.get("/admin/imports")).text
    assert (await client.get("/api/v2/practice/papers?subject_id=" + subject)).json()[
        "items"
    ][0]["title"] == title
    assert not (await client.get("/api/v2/learning/sessions/" + practice["id"])).json()[
        "read_only"
    ]
    assert (
        await session.get(Attempt, uuid.UUID(attempt["id"]))
    ).snapshot == original_snapshot
    audits = list(
        await session.scalars(
            select(AuditEvent).where(AuditEvent.action.like("%rename_batch"))
        )
    )
    assert any(a.summary.get("new_title") == title for a in audits)


async def test_linked_batches_and_unpublished_title(client, session, storage):
    subject, paper_id, units = await setup_questions(client, session)
    first = await session.scalar(
        select(QuestionImportBatch).where(
            QuestionImportBatch.published_paper_id == uuid.UUID(paper_id)
        )
    )
    first_id, code = str(first.id), first.batch_code
    second_id = await upload(client, code=code)
    await review(client, session, second_id)
    url = "/api/v1/admin/import-batches/" + second_id
    renamed = await client.patch(url + "/title", json={"title": "未发布标题"})
    assert renamed.status_code == 200, renamed.text
    assert (await session.get(ExamPaper, uuid.UUID(paper_id))).title != "未发布标题"
    assert (
        await client.post("/api/v1/admin/markdown-batches/" + second_id + "/publish")
    ).status_code == 200
    renamed = await client.patch(url + "/title", json={"title": "同步标题"})
    assert renamed.status_code == 200, renamed.text
    session.expire_all()
    assert (
        await session.get(QuestionImportBatch, uuid.UUID(first_id))
    ).title == "同步标题"
    response = await client.get("/admin/imports")
    assert 'data-linked-count="2"' in response.text
    assert (await client.delete(url)).status_code == 200
    deleted = (await client.get("/admin/imports?view=deleted")).text
    assert first_id in deleted and second_id in deleted
    assert (
        await client.post("/api/v1/admin/import-batches/" + first_id + "/restore")
    ).status_code == 200
    active = (await client.get("/admin/imports")).text
    assert first_id in active and second_id in active


async def test_delete_blocks_active_jobs_and_requires_admin(client, session, storage):
    from app.models import UserRole
    from sqlalchemy import delete

    subject, paper_id, units = await setup_questions(client, session)
    batch = await session.scalar(
        select(QuestionImportBatch).where(
            QuestionImportBatch.published_paper_id == uuid.UUID(paper_id)
        )
    )
    url = "/api/v1/admin/import-batches/" + str(batch.id)
    job = await session.scalar(select(Job).where(Job.batch_id == batch.id))
    job.status = "queued"
    job_id = job.id
    await session.commit()
    denied = await client.delete(url)
    assert denied.status_code == 409 and "未结束任务" in denied.text
    job = await session.get(Job, job_id)
    job.status = "succeeded"
    await session.commit()
    assert (await client.delete(url, headers={"If-Match": "0"})).status_code == 409
    await session.execute(delete(UserRole))
    await session.commit()
    assert (await client.delete(url)).status_code == 403
    assert (await client.post(url + "/restore")).status_code == 403
    assert (
        await client.patch(url + "/title", json={"title": "forbidden"})
    ).status_code == 403


async def test_publish_and_delete_serialize_without_restoring_deleted_paper(
    client, session, storage
):
    import asyncio
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.imports.paper_management import manage, lock_paper_identity
    from app.imports.markdown_workflow import publish
    from app.imports.service import ImportConflictError

    _, paper_id, _ = await setup_questions(client, session)
    batch = await session.scalar(
        select(QuestionImportBatch).where(
            QuestionImportBatch.published_paper_id == uuid.UUID(paper_id)
        )
    )
    bid = batch.id
    await session.commit()
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    async with factory() as deleting:
        await lock_paper_identity(deleting, bid)

        async def republish():
            async with factory() as publishing:
                try:
                    await publish(publishing, bid, storage)
                    await publishing.commit()
                    return "published"
                except ImportConflictError:
                    return "deleted"

        pending = asyncio.create_task(republish())
        try:
            await manage(deleting, bid, action="delete")
            await deleting.commit()
            assert await asyncio.wait_for(pending, timeout=10) == "deleted"
        finally:
            if not pending.done():
                pending.cancel()
    session.expire_all()
    assert (await session.get(ExamPaper, uuid.UUID(paper_id))).status == "retired"
