"""Small list projections, separate from the detailed historical document view."""

from collections import defaultdict
from sqlalchemy import func, select
from app.models import (
    QuestionImportBatch,
    QuestionImportItem,
    KnowledgeTaxonomyRelease,
    Job,
)


async def batch_list(session):
    rows = (
        await session.execute(
            select(QuestionImportBatch, KnowledgeTaxonomyRelease.version)
            .join(
                KnowledgeTaxonomyRelease,
                KnowledgeTaxonomyRelease.id == QuestionImportBatch.taxonomy_release_id,
            )
            .order_by(QuestionImportBatch.created_at.desc())
            .limit(50)
        )
    ).all()
    ids = [batch.id for batch, _ in rows]
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
        )
        for batch, version in rows
    ]
