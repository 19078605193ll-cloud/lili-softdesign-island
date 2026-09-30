"""Live paper metadata and access checks, separate from immutable answer snapshots."""

import uuid

from sqlalchemy import select

from app.core.errors import fail
from app.models import ExamPaper, PracticeQuestion


async def question_paper(session, qid, *, lock=False):
    query = select(ExamPaper).join(PracticeQuestion).where(PracticeQuestion.id == qid)
    if lock:
        query = query.with_for_update(read=True, of=ExamPaper)
    return await session.scalar(query.execution_options(populate_existing=True))


async def require_available(session, qid):
    paper = await question_paper(session, qid, lock=True)
    if paper and paper.status == "retired":
        raise fail(409, "PAPER_DELETED", "该试卷已删除，无法继续练习")
    return paper


async def session_title(session, row):
    if row.source not in {"paper", "similar", "question"}:
        return row.title
    paper = None
    if row.source == "paper" and row.source_id:
        paper = await session.get(ExamPaper, row.source_id)
    elif row.question_ids:
        paper = await question_paper(session, uuid.UUID(row.question_ids[0]))
    return paper.title if paper else row.title
