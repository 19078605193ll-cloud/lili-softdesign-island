"""One durable entry point for real questions, cached variants and generation."""

import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.errors import fail
from app.core.security import current_user
from app.h5.models import LearningSession, RelatedPractice, Variant, VariantAnswer
from app.h5.service import catalog, lock, owned
from app.h5.tutor import ACTIVE, Key, VariantIn, prepare_variant
from app.imports.dependencies import SessionDependency
from app.models import Attempt, Job, PracticeQuestionPart, QuestionKnowledgeAssignment

router = APIRouter(prefix="/api/v2", tags=["h5"])


class RelatedIn(BaseModel):
    session_id: uuid.UUID


async def unfinished(session, user_id, result):
    kind = result.get("kind")
    if kind == "question":
        practice = await session.get(LearningSession, uuid.UUID(result["session_id"]))
        if not practice or practice.attempts:
            return False
        from app.practice.service import load

        return bool(await load(session, [uuid.UUID(q) for q in practice.question_ids]))
    if kind == "job":
        job = await session.get(Job, uuid.UUID(result["job_id"]))
        if job and job.status in ACTIVE:
            return True
        if not job or job.status != "succeeded":
            return False
        vid = job.result.get("variant_id")
    elif kind == "variant":
        vid = result["variant_id"]
    else:
        return kind == "pending"
    if not vid:
        return False
    item = await session.get(Variant, uuid.UUID(vid))
    return bool(
        item
        and item.status == "approved"
        and not await session.get(VariantAnswer, (user_id, item.id))
    )


@router.post("/practice/questions/{qid}/related-practice")
async def related_practice(
    qid: uuid.UUID,
    payload: RelatedIn,
    request: Request,
    session: SessionDependency,
    idempotency_key: Key,
):
    user = await current_user(request)
    parent = await owned(session, LearningSession, payload.session_id, user.id)
    if str(qid) not in parent.question_ids:
        raise fail(422, "SESSION_QUESTION", "题目不属于本次练习")
    from app.h5.paper_state import require_available
    await require_available(session, qid)
    # Serialize across all source sessions for this user's question.
    await lock(session, f"related:{user.id}:{qid}")
    await lock(session, f"related-key:{user.id}:{idempotency_key}")
    record = await session.scalar(
        select(RelatedPractice).where(
            RelatedPractice.user_id == user.id,
            RelatedPractice.request_key == idempotency_key,
        )
    )
    if record:
        if record.question_id != qid or record.session_id != parent.id:
            raise fail(409, "IDEMPOTENCY_CONFLICT", "请求标识已使用")
        if record.result.get("kind") != "pending":
            return record.result
    else:
        previous = list(
            await session.scalars(
                select(RelatedPractice)
                .where(
                    RelatedPractice.user_id == user.id,
                    RelatedPractice.question_id == qid,
                )
                .order_by(RelatedPractice.created_at.desc(), RelatedPractice.id.desc())
            )
        )
        for old in previous:
            if await unfinished(session, user.id, old.result):
                if old.result.get("kind") == "pending":
                    record = old
                    break
                record = RelatedPractice(
                    user_id=user.id,
                    session_id=parent.id,
                    question_id=qid,
                    request_key=idempotency_key,
                    result=old.result,
                )
                session.add(record)
                await session.commit()
                return record.result
        if record is None:
            record = RelatedPractice(
                user_id=user.id,
                session_id=parent.id,
                question_id=qid,
                request_key=idempotency_key,
                result={"kind": "pending"},
            )
            session.add(record)
            await session.flush()

    nids = (
        select(QuestionKnowledgeAssignment.knowledge_node_id)
        .join(
            PracticeQuestionPart,
            PracticeQuestionPart.question_id == QuestionKnowledgeAssignment.question_id,
        )
        .where(
            PracticeQuestionPart.practice_question_id == qid,
            QuestionKnowledgeAssignment.role == "primary",
            QuestionKnowledgeAssignment.status == "confirmed",
        )
    )
    scored = dict(
        (
            await session.execute(
                select(
                    PracticeQuestionPart.practice_question_id,
                    func.count(
                        func.distinct(QuestionKnowledgeAssignment.knowledge_node_id)
                    ),
                )
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
                .group_by(PracticeQuestionPart.practice_question_id)
            )
        ).all()
    )
    answered = set(
        await session.scalars(
            select(Attempt.practice_question_id).where(Attempt.user_id == user.id)
        )
    )
    candidates = [
        (u, p)
        for u, p in await catalog(session, parent.subject_id)
        if u.id != qid and u.id not in answered and u.id in scored
    ]
    candidates.sort(key=lambda pair: (-scored[pair[0].id], str(pair[0].id)))
    if candidates:
        question, paper = candidates[0]
        practice = LearningSession(
            user_id=user.id,
            subject_id=parent.subject_id,
            source="similar",
            source_id=question.id,
            title=paper.title[:200],
            question_ids=[str(question.id)],
        )
        session.add(practice)
        await session.flush()
        record.result = {"kind": "question", "session_id": str(practice.id)}
    else:
        # Uses the same durable key after reload/crash, and commits the pending record
        # atomically with its generation job. No extra tutoring conversation is needed.
        result = await prepare_variant(
            VariantIn(question_id=qid), request, session, "related:" + str(record.id), commit=False
        )
        record.result = (
            {"kind": "variant", "variant_id": result["variant"]["id"]}
            if result.get("variant")
            else {"kind": "job", "job_id": result["job_id"]}
        )
    await session.commit()
    return record.result
