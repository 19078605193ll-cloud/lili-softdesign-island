"""Reversible published-paper management; never removes learner history or assets."""

from sqlalchemy import select, text

from app.core.errors import fail
from app.core.revisions import check_revision
from app.models import ExamPaper, Job, QuestionImportBatch


async def lock_paper_identity(session, batch_id):
    batch = await session.get(QuestionImportBatch, batch_id)
    if batch is None:
        raise fail(404, "NOT_FOUND", "导入批次不存在")
    identity = f"{batch.subject_id}:{batch.year}:{batch.period}:{batch.batch_code}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": identity},
    )
    paper = await session.scalar(
        select(ExamPaper)
        .where(
            ExamPaper.subject_id == batch.subject_id,
            ExamPaper.year == batch.year,
            ExamPaper.period == batch.period,
            ExamPaper.batch_code == batch.batch_code,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return batch, paper


async def manage(session, batch_id, *, action, title=None):
    initial, paper = await lock_paper_identity(session, batch_id)
    linked = bool(initial.published_paper_id)
    query = (
        select(QuestionImportBatch)
        .where(
            QuestionImportBatch.published_paper_id == paper.id
            if linked and paper
            else QuestionImportBatch.id == batch_id
        )
        .order_by(QuestionImportBatch.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    batches = list(await session.scalars(query))
    batch = next(b for b in batches if b.id == batch_id)
    session.info["audit_summary"] = {
        "operation": action,
        "batch_id": str(batch.id),
        "paper_id": str(paper.id) if linked else None,
        "batch_ids": [str(b.id) for b in batches],
        **({"old_title": batch.title, "new_title": title} if action == "title" else {}),
    }
    await check_revision(session, batch)
    if action != "title" and not linked:
        if action == "delete":
            return False
        raise fail(409, "NOT_PUBLISHED", "未发布批次不能恢复")
    if action == "delete":
        # Include unpublished additions sharing this paper identity.
        ids = select(QuestionImportBatch.id).where(
            QuestionImportBatch.subject_id == batch.subject_id,
            QuestionImportBatch.year == batch.year,
            QuestionImportBatch.period == batch.period,
            QuestionImportBatch.batch_code == batch.batch_code,
        )
        if await session.scalar(
            select(Job.id)
            .where(
                Job.batch_id.in_(ids),
                Job.status.in_(["queued", "running", "retry_wait"]),
            )
            .limit(1)
        ):
            raise fail(409, "TASK_ACTIVE", "关联批次仍有未结束任务，请等待完成后删除")
    old_title = batch.title
    if action == "title":
        for item in batches:
            item.title = title
        if linked:
            paper.title = title
    else:
        paper.status = "retired" if action == "delete" else "verified"
    for item in batches:
        if item.id != batch.id:
            item.revision += 1
    session.info["audit_summary"] = {
        "operation": action,
        "paper_id": str(paper.id) if linked else None,
        "batch_ids": [str(b.id) for b in batches],
        **({"old_title": old_title, "new_title": title} if action == "title" else {}),
    }
    return True
