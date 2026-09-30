import uuid
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Request, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from app.core.errors import fail
from app.core.security import current_user
from app.imports.dependencies import SessionDependency
from app.models import ExamSubject, ExamPaper, PracticeQuestion, Attempt
from app.practice.service import load
from app.h5.models import Preference, ExamPlan, LearningSession, Mark
from app.h5.service import catalog, nodes, node_units, owned, set_mark, metrics, lock
from app.h5.render import excerpt

router = APIRouter(prefix="/api/v2", tags=["h5"])


async def subject(session, sid):
    row = await session.get(ExamSubject, sid)
    if not row or not row.is_active:
        raise fail(404, "SUBJECT_NOT_FOUND", "科目不存在")
    return row


@router.get("/practice/subjects")
async def subjects(session: SessionDependency):
    rows = await session.scalars(
        select(ExamSubject)
        .where(ExamSubject.is_active.is_(True))
        .order_by(ExamSubject.name)
    )
    return {"items": [dict(id=str(s.id), code=s.code, name=s.name) for s in rows]}


class PreferencesIn(BaseModel):
    subject_id: uuid.UUID | None = None
    motto: str | None = Field(None, max_length=200)


@router.get("/learning/preferences")
async def preferences(request: Request, session: SessionDependency):
    user = await current_user(request)
    row = await session.get(Preference, user.id)
    return dict(
        subject_id=str(row.subject_id) if row and row.subject_id else None,
        motto=row.motto if row else "千里之行，始于足下。",
    )


@router.patch("/learning/preferences")
async def save_preferences(
    payload: PreferencesIn, request: Request, session: SessionDependency
):
    user = await current_user(request)
    await lock(session, f"preference:{user.id}")
    if payload.subject_id:
        await subject(session, payload.subject_id)
    row = await session.get(Preference, user.id)
    if not row:
        row = Preference(user_id=user.id)
        session.add(row)
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(row, key, value)
    await session.commit()
    return await preferences(request, session)


class PlanIn(BaseModel):
    subject_id: uuid.UUID
    title: str = Field(min_length=1, max_length=100)
    exam_date: date
    start_date: date

    @model_validator(mode="after")
    def valid_dates(self):
        if self.start_date > self.exam_date:
            raise ValueError("备考开始日不能晚于考试日")
        return self


@router.get("/learning/exam-plan")
async def exam_plan(
    subject_id: uuid.UUID, request: Request, session: SessionDependency
):
    user = await current_user(request)
    row = await session.get(ExamPlan, (user.id, subject_id))
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if not row:
        return dict(plan=None, today=today.isoformat())
    duration = (row.exam_date - row.start_date).days
    return dict(
        today=today.isoformat(),
        plan=dict(
            title=row.title,
            exam_date=row.exam_date,
            start_date=row.start_date,
            days_remaining=(row.exam_date - today).days,
            progress=max(
                0, min(100, round(100 * (today - row.start_date).days / duration))
            )
            if duration
            else (100 if today >= row.exam_date else 0),
        ),
    )


@router.put("/learning/exam-plan")
async def save_plan(payload: PlanIn, request: Request, session: SessionDependency):
    user = await current_user(request)
    await subject(session, payload.subject_id)
    await lock(session, f"plan:{user.id}:{payload.subject_id}")
    row = await session.get(ExamPlan, (user.id, payload.subject_id))
    if not row:
        row = ExamPlan(user_id=user.id, subject_id=payload.subject_id)
        session.add(row)
    for k, v in payload.model_dump().items():
        setattr(row, k, v)
    await session.commit()
    return await exam_plan(payload.subject_id, request, session)


@router.get("/learning/dashboard")
async def dashboard(
    subject_id: uuid.UUID, request: Request, session: SessionDependency
):
    user = await current_user(request)
    await subject(session, subject_id)
    return await metrics(session, user.id, subject_id)


@router.get("/learning/chapters")
async def chapters(subject_id: uuid.UUID, request: Request, session: SessionDependency):
    return {"items": (await dashboard(subject_id, request, session))["chapters"]}


@router.get("/practice/papers")
async def papers(
    subject_id: uuid.UUID,
    request: Request,
    session: SessionDependency,
    cursor: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    year: int | None = None,
):
    user = await current_user(request)
    rows = await catalog(session, subject_id)
    completed = set(await session.scalars(select(Attempt.practice_question_id)
        .join(PracticeQuestion).join(ExamPaper)
        .where(Attempt.user_id == user.id, ExamPaper.subject_id == subject_id).distinct()))
    data = {}
    for u, p in rows:
        if year and p.year != year:
            continue
        item = data.setdefault(
            p.id,
            dict(
                id=str(p.id),
                title=p.title,
                year=p.year,
                period=p.period,
                source="考生回忆版",
                total=0,
                completed=0,
            ),
        )
        item["total"] += 1
        item["completed"] += int(u.id in completed)
    items = list(data.values())
    recent = list(await session.scalars(select(LearningSession).where(
        LearningSession.user_id == user.id, LearningSession.subject_id == subject_id,
        LearningSession.source == "paper", LearningSession.source_id.in_(list(data)),
    ).order_by(LearningSession.updated_at.desc(), LearningSession.id.desc())))
    for pid, item in data.items():
        matching = [s for s in recent if s.source_id == pid]
        meaningful = [s for s in matching if s.attempts or s.drafts or s.position != 0]
        previous = next(iter(meaningful or matching), None)
        item["resume_position"] = previous.position if previous else None
    return dict(
        items=items[cursor : cursor + limit],
        next_cursor=cursor + limit if len(items) > cursor + limit else None,
    )


@router.get("/practice/search")
async def search(
    subject_id: uuid.UUID,
    session: SessionDependency,
    q: str = Query("", max_length=100),
    cursor: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
):
    tree = await nodes(session, subject_id)
    query = q.strip().casefold()
    found = [
        dict(id=str(n.id), name=n.name, type=n.node_type)
        for n in tree
        if query and query in " ".join([n.name, *n.aliases, *n.keywords]).casefold()
    ]
    return dict(
        items=found[cursor : cursor + limit],
        next_cursor=cursor + limit if len(found) > cursor + limit else None,
    )


@router.get("/practice/nodes/{node_id}/questions")
async def node_questions(
    node_id: uuid.UUID,
    session: SessionDependency,
    cursor: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
):
    from app.models import KnowledgeNode

    node = await session.get(KnowledgeNode, node_id)
    if not node:
        raise fail(404, "NODE_NOT_FOUND", "章节不存在")
    matching = await node_units(session, node.id, node.subject_id)
    ids = [u.id for u, _ in await catalog(session, node.subject_id) if u.id in matching]
    return dict(
        items=list((await load(session, ids[cursor : cursor + limit])).values()),
        next_cursor=cursor + limit if len(ids) > cursor + limit else None,
    )


class SessionIn(BaseModel):
    subject_id: uuid.UUID
    source: Literal[
        "paper", "node", "question", "wrong", "hesitant", "favorite", "similar"
    ]
    source_id: uuid.UUID
    mode: Literal["new", "resume"] = "new"


def session_read(s):
    return dict(
        id=str(s.id),
        subject_id=str(s.subject_id),
        source=s.source,
        source_id=str(s.source_id) if s.source_id else None,
        title=s.title,
        question_ids=s.question_ids,
        position=s.position,
        drafts=s.drafts,
        attempts=s.attempts,
    )


@router.post("/learning/sessions", status_code=201)
async def create_session(
    payload: SessionIn, request: Request, session: SessionDependency
):
    user = await current_user(request)
    await subject(session, payload.subject_id)
    rows = await catalog(session, payload.subject_id)
    title = "题目练习"
    if payload.source == "paper":
        rows = [(u, p) for u, p in rows if p.id == payload.source_id]
        title = rows[0][1].title if rows else title
    elif payload.source == "node":
        from app.models import KnowledgeNode

        node = await session.get(KnowledgeNode, payload.source_id)
        if not node or node.subject_id != payload.subject_id:
            raise fail(404, "NODE_NOT_FOUND", "章节不存在")
        matching = await node_units(session, node.id, node.subject_id)
        rows = [(u, p) for u, p in rows if u.id in matching]
        title = node.name
    else:
        rows = [(u, p) for u, p in rows if u.id == payload.source_id]
        if payload.source in {"wrong", "hesitant", "favorite"}:
            mark = await session.get(Mark, (user.id, payload.source_id, payload.source))
            if not mark or not mark.active:
                raise fail(409, "MARK_RESOLVED", "该记录已解除，请刷新列表")
            title = {
                "wrong": "错题重练",
                "hesitant": "犹豫题重练",
                "favorite": "收藏题练习",
            }[payload.source]
    if not rows:
        raise fail(404, "NO_QUESTIONS", "暂无可练习题目")
    if payload.mode == "resume" and payload.source in {"paper", "node"}:
        from app.h5.resume import resume_session
        return session_read(await resume_session(session, user.id, payload, rows, title))
    row = LearningSession(
        user_id=user.id,
        subject_id=payload.subject_id,
        source=payload.source,
        source_id=payload.source_id,
        title=title[:200],
        question_ids=[str(u.id) for u, _ in rows],
    )
    session.add(row)
    await session.commit()
    return session_read(row)


@router.get("/learning/sessions/{session_id}")
async def get_session(
    session_id: uuid.UUID, request: Request, session: SessionDependency
):
    user = await current_user(request)
    row = await owned(session, LearningSession, session_id, user.id)
    current = await load(session, [uuid.UUID(q) for q in row.question_ids if q not in row.attempts])
    positions = [i for i, q in enumerate(row.question_ids) if q in row.attempts or uuid.UUID(q) in current]
    notice = ""
    if positions and row.position not in positions:
        notice = "题库已更新，已为你继续上次的练习。"
        row.position = next((i for i in positions if i > row.position), positions[-1])
        await session.commit()
    from app.h5.paper_state import question_paper, session_title
    paper = await question_paper(session, uuid.UUID(row.question_ids[row.position])) if row.question_ids else None
    deleted = bool(paper and paper.status == "retired")
    if deleted:
        notice = "当前为历史回顾，可查看原来的答案、解析和对话。"
    return {**session_read(row), "title": await session_title(session, row),
            "available_positions": positions, "notice": notice, "read_only": deleted}


class DraftIn(BaseModel):
    position: int | None = Field(None, ge=0)
    question_id: uuid.UUID | None = None
    content_version: int | None = None
    answers: dict[str, str] = Field(default_factory=dict, max_length=100)


@router.patch("/learning/sessions/{session_id}")
async def save_session(
    session_id: uuid.UUID,
    payload: DraftIn,
    request: Request,
    session: SessionDependency,
):
    user = await current_user(request)
    row = await owned(session, LearningSession, session_id, user.id, write=True)
    if payload.position is not None:
        if payload.position >= len(row.question_ids):
            raise fail(422, "POSITION", "题目位置无效")
        row.position = payload.position
    if payload.question_id:
        qid = str(payload.question_id)
        if qid not in row.question_ids:
            raise fail(422, "SESSION_QUESTION", "题目不属于本次练习")
        data = (await load(session, [payload.question_id])).get(payload.question_id)
        if not data or data["content_version"] != payload.content_version:
            raise fail(409, "CONTENT_CHANGED", "题目已更新，请重新加载")
        valid = {p["id"]: {o["key"] for o in p["options"]} for p in data["parts"]}
        if any(k not in valid or v not in valid[k] for k, v in payload.answers.items()):
            raise fail(422, "INVALID_OPTION", "草稿选项无效")
        if qid not in row.attempts:
            row.drafts = {
                **row.drafts,
                qid: dict(
                    content_version=payload.content_version, answers=payload.answers
                ),
            }
    await session.commit()
    return session_read(row)


@router.get("/learning/notebook")
async def notebook(
    subject_id: uuid.UUID,
    request: Request,
    session: SessionDependency,
    type: Literal["wrong", "hesitant", "favorite"] = "wrong",
    cursor: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    user = await current_user(request)
    rows = list(
        (
            await session.execute(
                select(Mark, PracticeQuestion)
                .join(PracticeQuestion, PracticeQuestion.id == Mark.question_id)
                .join(ExamPaper)
                .where(
                    Mark.user_id == user.id,
                    Mark.active.is_(True),
                    Mark.kind == type,
                    ExamPaper.subject_id == subject_id,
                )
                .order_by(Mark.updated_at.desc(), Mark.question_id)
                .offset(cursor)
                .limit(limit + 1)
            )
        ).all()
    )
    current = await load(session, [u.id for _, u in rows[:limit]])
    tree = await nodes(session, subject_id)
    from app.models import PracticeQuestionPart, QuestionKnowledgeAssignment

    labels = {}
    assignments = list(
        (
            await session.execute(
                select(
                    PracticeQuestionPart.practice_question_id,
                    QuestionKnowledgeAssignment.knowledge_node_id,
                )
                .join(
                    QuestionKnowledgeAssignment,
                    QuestionKnowledgeAssignment.question_id
                    == PracticeQuestionPart.question_id,
                )
                .where(
                    PracticeQuestionPart.practice_question_id.in_(
                        [u.id for _, u in rows]
                    ),
                    QuestionKnowledgeAssignment.role == "primary",
                    QuestionKnowledgeAssignment.status == "confirmed",
                )
            )
        ).all()
    )
    for chapter in [n for n in tree if n.node_type == "chapter"]:
        from app.h5.service import descendants

        branch = descendants(chapter.id, tree)
        for qid, nid in assignments:
            if nid in branch:
                labels.setdefault(qid, chapter.name)
    items = []
    for mark, u in rows[:limit]:
        data = current.get(u.id)
        if not data and mark.attempt_id:
            attempt = await session.get(Attempt, mark.attempt_id)
            data = attempt.snapshot if attempt else None
        items.append(
            dict(
                practice_question_id=str(u.id),
                stem_excerpt=excerpt(data or {}),
                chapter_name=labels.get(u.id, "题目复习"),
                attempt_id=str(mark.attempt_id) if mark.attempt_id else None,
                can_practice=u.id in current,
                content_version=u.content_version,
            )
        )
    return dict(items=items, next_cursor=cursor + limit if len(rows) > limit else None)


class MarkIn(BaseModel):
    attempt_id: uuid.UUID | None = None


@router.get("/learning/questions/{qid}/marks")
async def marks(qid: uuid.UUID, request: Request, session: SessionDependency):
    user = await current_user(request)
    return {
        "items": [
            m.kind
            for m in await session.scalars(
                select(Mark).where(
                    Mark.user_id == user.id,
                    Mark.question_id == qid,
                    Mark.active.is_(True),
                )
            )
        ]
    }


@router.put("/learning/questions/{qid}/marks/{kind}")
async def add_mark(
    qid: uuid.UUID,
    kind: Literal["favorite", "hesitant"],
    payload: MarkIn,
    request: Request,
    session: SessionDependency,
):
    user = await current_user(request)
    if qid not in await load(session, [qid]):
        raise fail(404, "QUESTION_NOT_FOUND", "题目不可访问")
    if payload.attempt_id:
        attempt = await owned(session, Attempt, payload.attempt_id, user.id)
        if attempt.practice_question_id != qid:
            raise fail(422, "ATTEMPT_QUESTION", "作答与题目不匹配")
    await set_mark(session, user.id, qid, kind, True, payload.attempt_id)
    await session.commit()
    return {"active": True}


@router.delete("/learning/questions/{qid}/marks/{kind}")
async def remove_mark(
    qid: uuid.UUID,
    kind: Literal["wrong", "favorite", "hesitant"],
    request: Request,
    session: SessionDependency,
):
    user = await current_user(request)
    existing = await session.get(Mark, (user.id, qid, kind))
    if existing:
        await set_mark(session, user.id, qid, kind, False)
        await session.commit()
    return {"active": False}


@router.get("/practice/questions/{qid}/similar")
async def similar(qid: uuid.UUID, session: SessionDependency):
    from app.models import PracticeQuestionPart, QuestionKnowledgeAssignment

    current = (await load(session, [qid])).get(qid)
    if not current:
        raise fail(404, "QUESTION_NOT_FOUND", "题目不可访问")
    paper = await session.get(ExamPaper, uuid.UUID(current["source"]["paper_id"]))
    nids = list(
        await session.scalars(
            select(QuestionKnowledgeAssignment.knowledge_node_id)
            .join(
                PracticeQuestionPart,
                PracticeQuestionPart.question_id
                == QuestionKnowledgeAssignment.question_id,
            )
            .where(
                PracticeQuestionPart.practice_question_id == qid,
                QuestionKnowledgeAssignment.role == "primary",
                QuestionKnowledgeAssignment.status == "confirmed",
            )
        )
    )
    matching = set(
        await session.scalars(
            select(PracticeQuestionPart.practice_question_id)
            .join(
                QuestionKnowledgeAssignment,
                QuestionKnowledgeAssignment.question_id
                == PracticeQuestionPart.question_id,
            )
            .where(
                QuestionKnowledgeAssignment.knowledge_node_id.in_(nids),
                QuestionKnowledgeAssignment.role == "primary",
                QuestionKnowledgeAssignment.status == "confirmed",
            )
        )
    )
    ids = [
        u.id
        for u, _ in await catalog(session, paper.subject_id)
        if u.id != qid and u.id in matching
    ][:5]
    return {"items": list((await load(session, ids)).values())}
