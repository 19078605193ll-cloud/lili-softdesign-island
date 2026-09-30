"""Small list projections, separate from the detailed historical document view."""

from collections import defaultdict
from sqlalchemy import func, select, or_
from app.models import (
    QuestionImportBatch,
    QuestionImportItem,
    KnowledgeTaxonomyRelease,
    Job,
    ExamPaper,
)


async def batch_list(session, view="active"):
    rows = (
        await session.execute(
            select(QuestionImportBatch, KnowledgeTaxonomyRelease.version)
            .join(
                KnowledgeTaxonomyRelease,
                KnowledgeTaxonomyRelease.id == QuestionImportBatch.taxonomy_release_id,
            )
            .outerjoin(ExamPaper, ExamPaper.id == QuestionImportBatch.published_paper_id)
            .where(ExamPaper.status == "retired" if view == "deleted" else
                   or_(ExamPaper.id.is_(None), ExamPaper.status != "retired"))
            .order_by(QuestionImportBatch.created_at.desc())
            .limit(50)
        )
    ).all()
    ids = [batch.id for batch, _ in rows]
    paper_ids = [b.published_paper_id for b, _ in rows if b.published_paper_id]
    linked_counts = dict((await session.execute(select(
        QuestionImportBatch.published_paper_id, func.count()
    ).where(QuestionImportBatch.published_paper_id.in_(paper_ids))
      .group_by(QuestionImportBatch.published_paper_id))).all())
    counts = defaultdict(dict)
    for batch_id, status, count in await session.execute(
        select(QuestionImportItem.batch_id, QuestionImportItem.status, func.count())
        .where(QuestionImportItem.batch_id.in_(ids))
        .group_by(QuestionImportItem.batch_id, QuestionImportItem.status)
    ):
        counts[batch_id][status] = count
    failures = defaultdict(list)
    for job in await session.scalars(
        select(Job)
        .where(Job.batch_id.in_(ids), Job.status == "failed")
        .order_by(Job.created_at.desc())
    ):
        if len(failures[job.batch_id]) < 3:
            failures[job.batch_id].append(
                {"stage": job.kind, "reason": job.error_detail}
            )
    return [
        dict(
            id=str(batch.id),
            title=batch.title,
            year=batch.year,
            period=batch.period,
            batch_code=batch.batch_code,
            taxonomy_version=version,
            status=batch.status,
            item_counts=counts[batch.id],
            error_summary=batch.error_summary,
            failures=failures[batch.id],
            published_paper_id=batch.published_paper_id,
            linked_count=linked_counts.get(batch.published_paper_id, 0),
            revision=batch.revision,
            deleted=view == "deleted",
        )
        for batch, version in rows
    ]
