import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Request
from sqlalchemy import select

from app.core.errors import fail
from app.core.security import current_user, require
from app.h5.service import descendants, lock, nodes, owned
from app.imports.dependencies import SessionDependency
from app.models import ExamSubject, Job, KnowledgeNode, Outbox
from app.recall import service as svc
from app.recall.models import (
    Recall,
    RecallKnowledgeLink,
    RecallSession,
    RecallSessionBlank,
    RecallVersion,
)
from app.recall.schemas import (
    ActionIn,
    AnswerIn,
    CreateIn,
    DraftIn,
    GenerationBatchIn,
    RevisionIn,
    SessionIn,
    VersionIn,
)

router = APIRouter(prefix="/api/v2/learning", tags=["recall"])
admin = APIRouter(prefix="/api/v2/admin/recalls", tags=["recall-admin"])


def generation_job(row):
    return {
        "job_id": str(row.id),
        "knowledge_node_id": row.payload["knowledge_node_id"],
        "status": row.status,
        "result": row.result,
        "error_code": row.error_code,
        "error_detail": row.error_detail,
    }


@router.get("/recalls")
async def listing(
    request: Request,
    session: SessionDependency,
    subject_id: uuid.UUID,
    type: Literal["table", "sentence", "relation"] | None = None,
    node_id: uuid.UUID | None = None,
):
    user = await current_user(request)
    from app.config import get_settings

    if not get_settings().recall_enabled:
        return {
            "enabled": False,
            "items": [],
            "resume_session_id": None,
            "mastered": 0,
            "pending": 0,
            "completed": 0,
        }
    ids = None
    if node_id:
        node = await session.get(KnowledgeNode, node_id)
        if not node or node.subject_id != subject_id or node.status != "active":
            raise fail(404, "NODE_NOT_FOUND", "知识点不存在")
        ids = descendants(node_id, await nodes(session, subject_id))
    return dict(
        enabled=True, **await svc.list_recalls(session, user.id, subject_id, type, ids)
    )


@router.get("/recalls/{rid}")
async def detail(rid: uuid.UUID, request: Request, session: SessionDependency):
    await current_user(request)
    r, v = await svc.published(session, rid)
    data = await svc.version_payload(session, v)
    # Learners see canonical full knowledge, not grading aliases or future hints.
    return {
        "id": str(r.id),
        "subject_id": str(r.subject_id),
        "version_id": str(v.id),
        "title": v.title,
        "type": v.type,
        "content": v.content,
        "blanks": [
            {"key": b["key"], "label": b["label"], "answer": b["answer"]}
            for b in data["blanks"]
        ],
        "knowledge": await svc.knowledge(session, v.id),
    }


@router.get("/recalls/{rid}/questions")
async def questions(
    rid: uuid.UUID,
    request: Request,
    session: SessionDependency,
    version_id: uuid.UUID | None = None,
    cursor: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    await current_user(request)
    r, v = await svc.published(session, rid)
    if version_id:
        v = await session.get(RecallVersion, version_id)
        if not v or v.recall_id != r.id:
            raise fail(404, "RECALL_NOT_FOUND", "内容版本不存在")
    _, items = await svc.related_version(session, v.id)
    return {
        "items": items[cursor : cursor + limit],
        "total": len(items),
        "next_cursor": cursor + limit if len(items) > cursor + limit else None,
    }


@router.post("/recall-sessions", status_code=201)
async def start(payload: SessionIn, request: Request, session: SessionDependency):
    user = await current_user(request)
    from app.config import get_settings

    if not get_settings().learning_enabled:
        raise fail(503, "LEARNING_DISABLED", "作答服务暂不可用")
    row = await svc.create_session(session, user.id, payload.recall_id)
    result = await svc.session_data(session, row)
    await session.commit()
    return result


@router.get("/recall-sessions/{sid}")
async def read_session(sid: uuid.UUID, request: Request, session: SessionDependency):
    user = await current_user(request)
    svc.enabled()
    return await svc.session_data(
        session, await owned(session, RecallSession, sid, user.id)
    )


@router.patch("/recall-sessions/{sid}")
async def draft(
    sid: uuid.UUID, payload: DraftIn, request: Request, session: SessionDependency
):
    user = await current_user(request)
    s = await svc.writable(session, sid, user.id, payload.revision)
    if s.phase not in {"first", "retest"} or s.queue[s.position] != payload.blank_key:
        raise fail(409, "RECALL_POSITION", "当前空位已改变")
    state = await session.get(RecallSessionBlank, (s.id, payload.blank_key))
    state.draft = payload.answer
    s.revision += 1
    s.updated_at = svc.now()
    await session.flush()
    result = await svc.session_data(session, s)
    await session.commit()
    return result


@router.post("/recall-sessions/{sid}/answers")
async def answer(
    sid: uuid.UUID,
    payload: AnswerIn,
    request: Request,
    session: SessionDependency,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=200)],
):
    user = await current_user(request)
    svc.enabled()
    result = await svc.submit(session, sid, user.id, payload, idempotency_key)
    await session.commit()
    return result


@router.post("/recall-sessions/{sid}/retest")
async def retest(
    sid: uuid.UUID, payload: RevisionIn, request: Request, session: SessionDependency
):
    user = await current_user(request)
    result = await svc.retest(session, sid, user.id, payload.revision)
    await session.commit()
    return result


@router.get("/nodes/{nid}")
async def node_detail(nid: uuid.UUID, request: Request, session: SessionDependency):
    user = await current_user(request)
    node = await session.get(KnowledgeNode, nid)
    if not node or node.status != "active":
        raise fail(404, "NODE_NOT_FOUND", "知识点不存在")
    tree = await nodes(session, node.subject_id)
    ids = descendants(nid, tree)
    from app.config import get_settings

    recalls = (
        await svc.list_recalls(session, user.id, node.subject_id, node_ids=ids)
        if get_settings().recall_enabled
        else {"items": []}
    )
    related = await svc.related(session, node.subject_id, ids)
    return {
        "id": str(node.id),
        "subject_id": str(node.subject_id),
        "name": node.name,
        "description": node.description,
        "parent_id": str(node.parent_id) if node.parent_id else None,
        "children": [
            {"id": str(n.id), "name": n.name} for n in tree if n.parent_id == nid
        ],
        "recalls": recalls["items"],
        "questions": related,
    }


@admin.get("")
async def admin_list(request: Request, session: SessionDependency):
    await require(request, "recalls:read")
    rows = (
        await session.execute(
            select(Recall, RecallVersion)
            .join(RecallVersion, RecallVersion.recall_id == Recall.id)
            .order_by(Recall.created_at.desc(), RecallVersion.number.desc())
        )
    ).all()
    return {
        "items": [
            {
                "id": str(r.id),
                "version_id": str(v.id),
                "title": v.title,
                "type": v.type,
                "status": v.status,
                "number": v.number,
                "revision": v.revision,
                "active": r.active,
                "current": r.published_version_id == v.id,
            }
            for r, v in rows
        ]
    }


@admin.get("/generation-options")
async def generation_options(
    subject_id: uuid.UUID, request: Request, session: SessionDependency
):
    await require(request, "recalls:read")
    recalls = list(
        await session.scalars(
            select(Recall)
            .where(Recall.subject_id == subject_id)
            .order_by(Recall.created_at.desc())
        )
    )
    result = {}
    for recall in recalls:
        latest = await session.scalar(
            select(RecallVersion)
            .where(RecallVersion.recall_id == recall.id)
            .order_by(RecallVersion.number.desc())
            .limit(1)
        )
        if not latest:
            continue
        node_ids = list(
            await session.scalars(
                select(RecallKnowledgeLink.knowledge_node_id).where(
                    RecallKnowledgeLink.version_id == latest.id
                )
            )
        )
        item = {
            "id": str(recall.id),
            "title": latest.title,
            "number": latest.number,
            "status": latest.status,
            "active": recall.active,
        }
        for node_id in node_ids:
            result.setdefault(str(node_id), []).append(item)
    return {"items": result}


@admin.post("/generations", status_code=202)
async def create_generation(
    payload: GenerationBatchIn,
    request: Request,
    session: SessionDependency,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=200)],
):
    user = await require(request, "recalls:edit")
    from app.config import get_settings

    settings = get_settings()
    if not settings.recall_ai_generation_enabled:
        raise fail(503, "RECALL_AI_DISABLED", "主动回忆 AI 生成暂未开放")
    if not settings.tasks_enabled or not settings.writes_enabled:
        raise fail(503, "TASKS_DISABLED", "后台任务暂不可用")
    if not settings.ai_api_key or not (settings.ai_recall_model or settings.ai_text_model):
        raise fail(503, "AI_NOT_CONFIGURED", "请配置主动回忆生成模型和 AI_API_KEY")
    subject = await session.get(ExamSubject, payload.subject_id)
    if not subject or not subject.is_active:
        raise fail(422, "SUBJECT_NOT_FOUND", "科目不存在")
    node_ids = [item.knowledge_node_id for item in payload.items]
    found = {
        node.id: node
        for node in await session.scalars(
            select(KnowledgeNode).where(
                KnowledgeNode.id.in_(node_ids),
                KnowledgeNode.subject_id == payload.subject_id,
                KnowledgeNode.status == "active",
                KnowledgeNode.node_type == "topic",
            )
        )
    }
    if len(found) != len(node_ids):
        raise fail(422, "RECALL_KNOWLEDGE", "请选择同科目的有效知识点")
    request_hash = svc.fingerprint(payload.model_dump(mode="json"))
    generation_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"recall-generation:{user.id}:{idempotency_key}"
    )
    await lock(session, f"recall-generation:{generation_id}")
    old = list(
        await session.scalars(
            select(Job)
            .where(
                Job.kind == "recall_generate",
                Job.payload["generation_request_id"].astext == str(generation_id),
            )
        )
    )
    old.sort(key=lambda job: job.payload.get("index", 0))
    if old:
        if len(old) != len(payload.items) or any(
            job.payload.get("request_hash") != request_hash for job in old
        ):
            raise fail(409, "IDEMPOTENCY_CONFLICT", "此标识已用于不同生成请求")
        return {"request_id": str(generation_id), "items": [generation_job(j) for j in old]}
    from app.recall.generation import RecallPromptError, load_prompt
    try:
        load_prompt()
    except RecallPromptError as exc:
        raise fail(503, exc.code, str(exc)) from exc
    jobs = []
    for index, item in enumerate(payload.items):
        base = {}
        if item.target.mode == "new_version":
            recall = await session.get(Recall, item.target.recall_id)
            if not recall or recall.subject_id != payload.subject_id:
                raise fail(422, "RECALL_TARGET", "要生成新版本的内容不存在")
            latest = await session.scalar(
                select(RecallVersion)
                .where(RecallVersion.recall_id == recall.id)
                .order_by(RecallVersion.number.desc())
                .limit(1)
            )
            linked = latest and await session.get(
                RecallKnowledgeLink, (latest.id, item.knowledge_node_id)
            )
            if not linked:
                raise fail(422, "RECALL_TARGET", "所选内容未关联该知识点")
            base = {
                "recall_id": str(recall.id),
                "base_version_id": str(latest.id),
                "base_revision": latest.revision,
            }
        job_payload = {
            "generation_request_id": str(generation_id),
            "request_hash": request_hash,
            "index": index,
            "subject_id": str(payload.subject_id),
            "knowledge_node_id": str(item.knowledge_node_id),
            "type": item.type,
            "target_mode": item.target.mode,
            "instruction": payload.instruction,
            **base,
        }
        job = Job(
            user_id=user.id,
            batch_id=None,
            kind="recall_generate",
            scope=f"recall:{generation_id}:{item.knowledge_node_id}",
            idempotency_key=idempotency_key,
            input_hash=svc.fingerprint(job_payload),
            payload=job_payload,
            request_id=str(generation_id),
        )
        session.add(job)
        await session.flush()
        session.add(Outbox(job_id=job.id))
        jobs.append(job)
    await session.commit()
    return {"request_id": str(generation_id), "items": [generation_job(j) for j in jobs]}


@admin.get("/generations/{request_id}")
async def read_generation(
    request_id: uuid.UUID, request: Request, session: SessionDependency
):
    await require(request, "recalls:read")
    rows = list(
        await session.scalars(
            select(Job)
            .where(
                Job.kind == "recall_generate",
                Job.payload["generation_request_id"].astext == str(request_id),
            )
        )
    )
    rows.sort(key=lambda job: job.payload.get("index", 0))
    if not rows:
        raise fail(404, "GENERATION_NOT_FOUND", "生成任务不存在")
    return {"request_id": str(request_id), "items": [generation_job(j) for j in rows]}


@admin.post("/generation-jobs/{job_id}/retry", status_code=202)
async def retry_generation(
    job_id: uuid.UUID, request: Request, session: SessionDependency
):
    await require(request, "recalls:edit")
    job = await session.scalar(
        select(Job).where(Job.id == job_id).with_for_update()
    )
    if not job or job.kind != "recall_generate":
        raise fail(404, "GENERATION_NOT_FOUND", "生成任务不存在")
    if job.status != "failed":
        raise fail(409, "JOB_STATE", "只有失败任务可以重试")
    job.status = "queued"
    job.retries = 0
    job.error_code = job.error_detail = None
    job.result = None
    job.available_at = svc.now()
    outbox = await session.get(Outbox, job.id)
    outbox.sent_at = None
    await session.commit()
    return generation_job(job)


@admin.post("", status_code=201)
async def admin_create(payload: CreateIn, request: Request, session: SessionDependency):
    user = await require(request, "recalls:edit")
    subject = await session.get(ExamSubject, payload.subject_id)
    if not subject or not subject.is_active:
        raise fail(422, "SUBJECT_NOT_FOUND", "科目不存在")
    await lock(session, f"recall-slug:{payload.subject_id}:{payload.slug}")
    if await session.scalar(
        select(Recall.id).where(
            Recall.subject_id == payload.subject_id, Recall.slug == payload.slug
        )
    ):
        raise fail(409, "RECALL_SLUG", "内容标识已存在")
    r = Recall(subject_id=payload.subject_id, slug=payload.slug)
    session.add(r)
    await session.flush()
    v = await svc.add_version(
        session,
        r,
        VersionIn.model_validate(payload.model_dump(exclude={"subject_id", "slug"})),
        user.id,
    )
    await session.commit()
    return {"id": str(r.id), "version_id": str(v.id)}


@admin.get("/versions/{vid}")
async def admin_detail(vid: uuid.UUID, request: Request, session: SessionDependency):
    await require(request, "recalls:read")
    v = await session.get(RecallVersion, vid)
    if not v:
        raise fail(404, "RECALL_NOT_FOUND", "版本不存在")
    r = await session.get(Recall, v.recall_id)
    return dict(
        id=str(r.id),
        version_id=str(v.id),
        subject_id=str(r.subject_id),
        slug=r.slug,
        number=v.number,
        revision=v.revision,
        status=v.status,
        review_note=v.review_note,
        **await svc.version_payload(session, v),
    )


@admin.get("/versions/{vid}/preview")
async def admin_preview(vid: uuid.UUID, request: Request, session: SessionDependency):
    await require(request, "recalls:read")
    v = await session.get(RecallVersion, vid)
    if not v:
        raise fail(404, "RECALL_NOT_FOUND", "版本不存在")
    r = await session.get(Recall, v.recall_id)
    payload = await svc.version_payload(session, v)
    return {
        "id": str(r.id),
        "version_id": str(v.id),
        "subject_id": str(r.subject_id),
        "title": v.title,
        "type": v.type,
        "content": v.content,
        "blanks": payload["blanks"],
    }


@admin.post("/seed-pilots")
async def seed_pilots(request: Request, session: SessionDependency):
    user = await require(request, "recalls:edit")
    from app.recall.pilots import seed

    ids = await seed(session, user.id)
    await session.commit()
    return {"ids": ids, "status": "drafts_only"}


@admin.post("/{rid}/versions", status_code=201)
async def admin_version(
    rid: uuid.UUID,
    payload: VersionIn,
    request: Request,
    session: SessionDependency,
    if_match: Annotated[str, Header()],
):
    user = await require(request, "recalls:edit")
    r = await session.scalar(select(Recall).where(Recall.id == rid).with_for_update())
    if not r:
        raise fail(404, "RECALL_NOT_FOUND", "内容不存在")
    latest = await session.scalar(
        select(RecallVersion)
        .where(RecallVersion.recall_id == rid)
        .order_by(RecallVersion.number.desc())
        .limit(1)
    )
    if if_match != f"{latest.id}:{latest.revision}":
        raise fail(409, "CONTENT_CHANGED", "内容已有新版本，请重新打开最新版本编辑")
    v = await svc.add_version(session, r, payload, user.id)
    await session.commit()
    return {"id": str(r.id), "version_id": str(v.id)}


@admin.post("/versions/{vid}/{action}")
async def admin_action(
    vid: uuid.UUID,
    action: Literal["submit", "approve", "reject", "publish", "unpublish"],
    payload: ActionIn,
    request: Request,
    session: SessionDependency,
):
    permission = {
        "submit": "edit",
        "approve": "review",
        "reject": "review",
        "publish": "publish",
        "unpublish": "publish",
    }[action]
    user = await require(request, "recalls:" + permission)
    # Lock parent first, matching edit/publish/start lock order.
    found = await session.get(RecallVersion, vid)
    if not found:
        raise fail(404, "RECALL_NOT_FOUND", "版本不存在")
    r = await session.scalar(
        select(Recall).where(Recall.id == found.recall_id).with_for_update()
    )
    v = await session.scalar(
        select(RecallVersion)
        .where(RecallVersion.id == vid)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if v.revision != payload.revision:
        raise fail(409, "CONTENT_CHANGED", "内容已更新，请刷新")
    expected = {
        "submit": "draft",
        "approve": "pending",
        "reject": "pending",
        "publish": "approved",
        "unpublish": "published",
    }[action]
    direct_approval = action == "approve" and v.status == "draft"
    if direct_approval and "recalls:edit" not in user.permissions:
        raise fail(403, "PERMISSION_DENIED", "没有直接审核草稿的权限")
    if v.status != expected and not direct_approval:
        raise fail(409, "RECALL_WORKFLOW", "当前状态不允许此操作")
    data = VersionIn.model_validate(await svc.version_payload(session, v))
    await svc.validate_nodes(session, r.subject_id, data.knowledge_node_ids)
    if action == "submit":
        v.status = "pending"
    elif action == "approve":
        v.status, v.reviewed_hash, v.reviewed_by, v.reviewed_at = (
            "approved",
            v.content_hash,
            user.id,
            svc.now(),
        )
    elif action == "reject":
        v.status, v.reviewed_hash = "draft", None
    elif action == "publish":
        if (
            v.reviewed_hash != v.content_hash
            or svc.fingerprint(data.model_dump(mode="json")) != v.content_hash
        ):
            raise fail(409, "RECALL_REVIEW", "内容与审核版本不一致")
        current = (
            await session.get(RecallVersion, r.published_version_id)
            if r.published_version_id
            else None
        )
        if current and current.number > v.number:
            raise fail(409, "RECALL_VERSION", "不能用旧版本覆盖新版")
        # Publication and removal of old learning commit atomically. Draft and
        # review actions never reset users' progress.
        await svc.clear_obsolete_sessions(session, r.id, v.id)
        v.status, v.published_by, v.published_at = "published", user.id, svc.now()
        r.published_version_id, r.active = v.id, True
    else:
        if r.published_version_id != v.id:
            raise fail(409, "RECALL_VERSION", "只能下架当前发布版本")
        r.active = False
    v.review_note = payload.note
    v.revision += 1
    await session.commit()
    return {"version_id": str(v.id), "status": v.status, "revision": v.revision}
