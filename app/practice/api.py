import base64
import json
import uuid
from fastapi import APIRouter, Query
from sqlalchemy import func, select, tuple_
from app.imports.dependencies import SessionDependency
from app.models import ExamPaper, PracticeQuestion, PracticeQuestionPart, Question
from app.practice.service import load, visible
from app.core.errors import fail
from app.practice.schemas import QuestionPage, QuestionRead, SolutionRead

router = APIRouter(prefix="/api/v2/practice", tags=["practice-v2"])


@router.get("/papers/{paper_id}/questions", response_model=QuestionPage)
async def list_questions(
    paper_id: uuid.UUID,
    session: SessionDependency,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=100),
):
    numbers = (
        select(
            PracticeQuestionPart.practice_question_id.label("id"),
            func.min(Question.question_no).label("number"),
        )
        .join(Question)
        .group_by(PracticeQuestionPart.practice_question_id)
        .subquery()
    )
    query = (
        select(PracticeQuestion.id, numbers.c.number)
        .join(ExamPaper)
        .join(numbers, numbers.c.id == PracticeQuestion.id)
        .where(PracticeQuestion.paper_id == paper_id, *visible())
    )
    if cursor:
        try:
            number, uid = json.loads(base64.urlsafe_b64decode(cursor).decode())
            query = query.where(
                tuple_(numbers.c.number, PracticeQuestion.id)
                > tuple_(int(number), uuid.UUID(uid))
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise fail(422, "INVALID_CURSOR", "分页游标无效") from exc
    rows = (
        await session.execute(
            query.order_by(numbers.c.number, PracticeQuestion.id).limit(limit + 1)
        )
    ).all()
    data = await load(session, [r.id for r in rows[:limit]])
    next_cursor = (
        base64.urlsafe_b64encode(
            json.dumps([rows[limit - 1].number, str(rows[limit - 1].id)]).encode()
        ).decode()
        if len(rows) > limit
        else None
    )
    return {"items": [data[r.id] for r in rows[:limit]], "next_cursor": next_cursor}


@router.get("/questions/{unit_id}", response_model=QuestionRead)
async def question(unit_id: uuid.UUID, session: SessionDependency):
    data = await load(session, [unit_id])
    if unit_id not in data:
        raise fail(404, "QUESTION_NOT_FOUND", "题目不存在")
    return data[unit_id]


@router.get("/questions/{unit_id}/solution", response_model=SolutionRead)
async def solution(unit_id: uuid.UUID, session: SessionDependency):
    data = await load(session, [unit_id], answers=True)
    if unit_id not in data:
        raise fail(404, "QUESTION_NOT_FOUND", "题目不存在")
    q = data[unit_id]
    return {
        "id": q["id"],
        "content_version": q["content_version"],
        "explanation_markdown": q["explanation_markdown"],
        "explanation_html": q["explanation_html"],
        "parts": [
            {k: p[k] for k in ("id", "correct_option_keys", "explanation_markdown", "explanation_html")}
            for p in q["parts"]
        ],
    }
