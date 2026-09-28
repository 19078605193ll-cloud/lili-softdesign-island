import hashlib
import json
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, text

from app.config import get_settings
from app.core.errors import fail
from app.core.security import current_user
from app.imports.dependencies import SessionDependency, StorageDependency
from app.models import (
    Attempt,
    AttemptPart,
    AttemptAsset,
    PracticeQuestion,
    QuestionKnowledgeAssignment,
    QuestionAsset,
)
from app.practice.service import load, grade

router = APIRouter(prefix="/api/v2/learning", tags=["learning"])


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    practice_question_id: uuid.UUID
    content_version: int = Field(ge=1)
    answers: dict[uuid.UUID, str]


async def result(session, attempt):
    parts = await session.scalars(
        select(AttemptPart)
        .where(AttemptPart.attempt_id == attempt.id)
        .order_by(AttemptPart.question_id)
    )
    return dict(
        id=str(attempt.id),
        practice_question_id=str(attempt.practice_question_id),
        content_version=attempt.content_version,
        score=str(attempt.score),
        created_at=attempt.created_at.isoformat(),
        parts=[
            dict(
                question_id=str(p.question_id),
                answer=p.answer,
                correct=p.correct,
                score=str(p.score),
            )
            for p in parts
        ],
    )


@router.post("/attempts", status_code=201)
async def submit(
    request: Request,
    response: Response,
    payload: Submission,
    session: SessionDependency,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=200)],
):
    if not get_settings().learning_enabled:
        raise fail(503, "LEARNING_DISABLED", "作答服务暂不可用")
    user = await current_user(request)
    raw = payload.model_dump(mode="json")
    raw["answers"] = {k: v.upper() for k, v in raw["answers"].items()}
    request_hash = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": f"attempt:{user.id}:{idempotency_key}"},
    )
    old = await session.scalar(
        select(Attempt).where(
            Attempt.user_id == user.id, Attempt.idempotency_key == idempotency_key
        )
    )
    if old:
        if old.request_hash != request_hash:
            raise fail(409, "IDEMPOTENCY_CONFLICT", "此提交标识已用于其他答案")
        response.status_code = 200
        return await result(session, old)
    unit = await session.scalar(
        select(PracticeQuestion)
        .where(PracticeQuestion.id == payload.practice_question_id)
        .with_for_update()
    )
    if not unit:
        raise fail(404, "QUESTION_NOT_FOUND", "题目不存在")
    if unit.content_version != payload.content_version:
        raise fail(409, "CONTENT_CHANGED", "题目已更新，请重新加载")
    data = (await load(session, [unit.id], answers=True)).get(unit.id)
    if not data:
        raise fail(404, "QUESTION_NOT_FOUND", "题目不存在")
    parts, score = grade(data, raw["answers"])
    attempt = Attempt(
        user_id=user.id,
        practice_question_id=unit.id,
        content_version=unit.content_version,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        snapshot=data,
        answers=raw["answers"],
        score=score,
    )
    session.add(attempt)
    await session.flush()
    qids = [uuid.UUID(p["question_id"]) for p in parts]
    assignments = list(
        await session.scalars(
            select(QuestionKnowledgeAssignment).where(
                QuestionKnowledgeAssignment.question_id.in_(qids),
                QuestionKnowledgeAssignment.status == "confirmed",
            )
        )
    )
    for part in parts:
        qid = uuid.UUID(part["question_id"])
        session.add(
            AttemptPart(
                attempt_id=attempt.id,
                question_id=qid,
                answer=part["answer"],
                correct=part["correct"],
                score=part["score"],
                knowledge=[
                    dict(
                        node_id=str(a.knowledge_node_id),
                        role=a.role,
                        release_id=str(a.taxonomy_release_id),
                    )
                    for a in assignments
                    if a.question_id == qid
                ],
            )
        )
    for asset in set(
        re.findall(r"/api/v1/question-assets/([0-9a-fA-F-]{36})", json.dumps(data))
    ):
        session.add(AttemptAsset(attempt_id=attempt.id, asset_id=uuid.UUID(asset)))
    await session.flush()
    output = await result(session, attempt)
    await session.commit()
    return output


@router.get("/attempts")
async def history(
    request: Request,
    session: SessionDependency,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    user = await current_user(request)
    rows = await session.scalars(
        select(Attempt)
        .where(Attempt.user_id == user.id)
        .order_by(Attempt.created_at.desc(), Attempt.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return {
        "items": [
            dict(
                id=str(a.id),
                practice_question_id=str(a.practice_question_id),
                content_version=a.content_version,
                score=str(a.score),
                created_at=a.created_at.isoformat(),
            )
            for a in rows
        ]
    }


@router.get("/attempts/{attempt_id}")
async def detail(attempt_id: uuid.UUID, request: Request, session: SessionDependency):
    user = await current_user(request)
    attempt = await session.scalar(
        select(Attempt).where(Attempt.id == attempt_id, Attempt.user_id == user.id)
    )
    if not attempt:
        raise fail(404, "ATTEMPT_NOT_FOUND", "记录不存在")
    snapshot = json.loads(
        re.sub(
            r"/api/v1/question-assets/([0-9a-fA-F-]{36})",
            lambda m: f"/api/v2/learning/attempts/{attempt.id}/assets/{m[1]}",
            json.dumps(attempt.snapshot),
        )
    )
    return dict(await result(session, attempt), snapshot=snapshot)


@router.get("/attempts/{attempt_id}/assets/{asset_id}")
async def history_asset(
    attempt_id: uuid.UUID,
    asset_id: uuid.UUID,
    request: Request,
    session: SessionDependency,
    storage: StorageDependency,
):
    user = await current_user(request)
    asset = await session.scalar(
        select(QuestionAsset)
        .join(AttemptAsset)
        .join(Attempt)
        .where(
            Attempt.id == attempt_id,
            Attempt.user_id == user.id,
            QuestionAsset.id == asset_id,
        )
    )
    if asset is None or not storage.resolve(asset.storage_path).is_file():
        raise fail(404, "ASSET_NOT_FOUND", "图片不存在")
    return FileResponse(storage.resolve(asset.storage_path), media_type=asset.mime_type)
