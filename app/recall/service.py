import hashlib
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.config import get_settings
from app.core.errors import fail
from app.h5.service import lock, owned
from app.models import (
    ExamPaper,
    KnowledgeNode,
    PracticeQuestion,
    PracticeQuestionPart,
    Question,
    QuestionKnowledgeAssignment,
)
from app.practice.service import load, visible
from app.recall.models import (
    Recall,
    RecallBlank,
    RecallKnowledgeLink,
    RecallSession,
    RecallSessionBlank,
    RecallSubmission,
    RecallVersion,
)
from app.recall.schemas import Normalization, VersionIn, normalize
from app.recall import grading


def now():
    return datetime.now(UTC)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def enabled():
    if not get_settings().recall_enabled:
        raise fail(503, "RECALL_DISABLED", "主动回忆暂未开放")


async def published(db, rid, *, write_guard=False):
    enabled()
    query = select(Recall).where(Recall.id == rid)
    if write_guard:
        query = query.with_for_update(read=True)
    r = await db.scalar(query.execution_options(populate_existing=True))
    if not r or not r.active or not r.published_version_id:
        raise fail(404, "RECALL_NOT_FOUND", "内容尚未发布或已下架")
    return r, await db.get(RecallVersion, r.published_version_id)


async def version_payload(db, version):
    blanks = list(
        await db.scalars(
            select(RecallBlank)
            .where(RecallBlank.version_id == version.id)
            .order_by(RecallBlank.position)
        )
    )
    nids = list(
        await db.scalars(
            select(RecallKnowledgeLink.knowledge_node_id)
            .where(RecallKnowledgeLink.version_id == version.id)
            .order_by(RecallKnowledgeLink.knowledge_node_id)
        )
    )
    return {
        "title": version.title,
        "content": version.content,
        "source": version.source,
        "provenance": version.provenance,
        "knowledge_node_ids": [str(n) for n in nids],
        "blanks": [
            {
                "key": b.key,
                "label": b.label,
                "answer": b.answer,
                "acceptable_answers": b.acceptable_answers,
                "normalization": b.normalization,
                "hints": b.hints,
            }
            for b in blanks
        ],
    }


async def validate_nodes(db, subject_id, ids):
    found = list(
        await db.scalars(
            select(KnowledgeNode).where(
                KnowledgeNode.id.in_(ids),
                KnowledgeNode.subject_id == subject_id,
                KnowledgeNode.status == "active",
                KnowledgeNode.node_type == "topic",
            )
        )
    )
    if len(found) != len(set(ids)):
        raise fail(422, "RECALL_KNOWLEDGE", "请选择同科目的有效知识点")


async def add_version(db, recall, payload: VersionIn, user_id):
    await validate_nodes(db, recall.subject_id, payload.knowledge_node_ids)
    number = (
        await db.scalar(
            select(func.max(RecallVersion.number)).where(
                RecallVersion.recall_id == recall.id
            )
        )
        or 0
    ) + 1
    data = payload.model_dump(mode="json")
    data["knowledge_node_ids"] = sorted(data["knowledge_node_ids"])
    v = RecallVersion(
        recall_id=recall.id,
        number=number,
        title=payload.title,
        type=payload.content.type,
        content=payload.content.model_dump(mode="json"),
        source=payload.source,
        provenance=payload.provenance,
        content_hash=fingerprint(data),
        created_by=user_id,
    )
    db.add(v)
    await db.flush()
    for i, blank in enumerate(payload.blanks):
        db.add(
            RecallBlank(version_id=v.id, position=i, **blank.model_dump(mode="json"))
        )
    for nid in payload.knowledge_node_ids:
        db.add(RecallKnowledgeLink(version_id=v.id, knowledge_node_id=nid))
    await db.flush()
    return v


async def knowledge(db, vid):
    rows = await db.scalars(
        select(KnowledgeNode)
        .join(
            RecallKnowledgeLink,
            RecallKnowledgeLink.knowledge_node_id == KnowledgeNode.id,
        )
        .where(RecallKnowledgeLink.version_id == vid)
        .order_by(KnowledgeNode.sort_order, KnowledgeNode.code)
    )
    return [{"id": str(n.id), "name": n.name, "code": n.code} for n in rows]


async def related(db, subject_id, node_ids):
    if not node_ids:
        return []
    rows = (
        await db.execute(
            select(
                PracticeQuestion.id,
                QuestionKnowledgeAssignment.role,
                KnowledgeNode.id,
                KnowledgeNode.name,
                ExamPaper.year,
            )
            .join(ExamPaper, ExamPaper.id == PracticeQuestion.paper_id)
            .join(
                PracticeQuestionPart,
                PracticeQuestionPart.practice_question_id == PracticeQuestion.id,
            )
            .join(Question, Question.id == PracticeQuestionPart.question_id)
            .join(
                QuestionKnowledgeAssignment,
                QuestionKnowledgeAssignment.question_id == Question.id,
            )
            .join(
                KnowledgeNode,
                KnowledgeNode.id == QuestionKnowledgeAssignment.knowledge_node_id,
            )
            .where(
                ExamPaper.subject_id == subject_id,
                KnowledgeNode.id.in_(node_ids),
                QuestionKnowledgeAssignment.status == "confirmed",
                Question.source_type.in_(["official", "recalled"]),
                *visible(),
            )
        )
    ).all()
    groups = {}
    for qid, role, nid, name, year in rows:
        g = groups.setdefault(qid, {"primary": False, "nodes": {}, "year": year})
        g["primary"] |= role == "primary"
        g["nodes"][str(nid)] = name
    ids = sorted(
        groups,
        key=lambda q: (
            -int(groups[q]["primary"]),
            -len(groups[q]["nodes"]),
            -groups[q]["year"],
            str(q),
        ),
    )
    data = await load(db, ids)
    return [
        {
            **data[q],
            "matched_knowledge": [
                {"id": n, "name": name} for n, name in groups[q]["nodes"].items()
            ],
        }
        for q in ids
        if q in data
    ]


async def related_version(db, vid, subject_id=None):
    v = await db.get(RecallVersion, vid)
    if not v or v.status != "published":
        raise fail(404, "RECALL_NOT_FOUND", "内容尚未发布")
    r, _ = await published(db, v.recall_id)
    if subject_id and subject_id != r.subject_id:
        raise fail(422, "RECALL_SUBJECT", "科目不匹配")
    ids = list(
        await db.scalars(
            select(RecallKnowledgeLink.knowledge_node_id).where(
                RecallKnowledgeLink.version_id == vid
            )
        )
    )
    return v, await related(db, r.subject_id, ids)


async def list_recalls(db, user_id, subject_id, type=None, node_ids=None):
    query = (
        select(Recall, RecallVersion)
        .join(RecallVersion, RecallVersion.id == Recall.published_version_id)
        .where(Recall.subject_id == subject_id, Recall.active.is_(True))
    )
    if type:
        query = query.where(RecallVersion.type == type)
    if node_ids is not None:
        query = query.where(
            RecallVersion.id.in_(
                select(RecallKnowledgeLink.version_id).where(
                    RecallKnowledgeLink.knowledge_node_id.in_(node_ids)
                )
            )
        )
    rows = (await db.execute(query.order_by(RecallVersion.created_at, Recall.id))).all()
    rids = [r.id for r, _ in rows]
    sessions = list(
        await db.scalars(
            select(RecallSession)
            .where(RecallSession.user_id == user_id, RecallSession.recall_id.in_(rids))
            .order_by(RecallSession.updated_at.desc(), RecallSession.id.desc())
        )
    )
    states = list(
        await db.scalars(
            select(RecallSessionBlank).where(
                RecallSessionBlank.session_id.in_([s.id for s in sessions])
            )
        )
    )
    by_session = {}
    for b in states:
        by_session.setdefault(b.session_id, []).append(b)
    counts = dict(
        (
            await db.execute(
                select(RecallBlank.version_id, func.count())
                .where(RecallBlank.version_id.in_([v.id for _, v in rows]))
                .group_by(RecallBlank.version_id)
            )
        ).all()
    )
    items = []
    for r, v in rows:
        active = next(
            (s for s in sessions if s.recall_id == r.id and s.phase != "completed"),
            None,
        )
        latest = next((s for s in sessions if s.version_id == v.id), None)
        bs = by_session.get(latest.id, []) if latest else []
        items.append(
            {
                "id": str(r.id),
                "version_id": str(v.id),
                "title": v.title,
                "type": v.type,
                "total_blanks": counts.get(v.id, 0),
                "mastered": sum(b.mastered for b in bs),
                "pending": sum(b.needs_retest for b in bs),
                "completed": bool(latest and latest.phase == "completed"),
                "session_id": str(active.id) if active else None,
                "phase": active.phase if active else None,
            }
        )
    active = next((s for s in sessions if s.phase != "completed"), None)
    return {
        "items": items,
        "resume_session_id": str(active.id) if active else None,
        "resume_recall_id": str(active.recall_id) if active else None,
        "resume_phase": active.phase if active else None,
        "mastered": sum(i["mastered"] for i in items),
        "pending": sum(i["pending"] for i in items),
        "completed": sum(i["completed"] for i in items),
    }


async def session_data(db, s):
    v = await db.get(RecallVersion, s.version_id)
    r = await db.get(Recall, s.recall_id)
    blanks = list(
        await db.scalars(
            select(RecallBlank)
            .where(RecallBlank.version_id == v.id)
            .order_by(RecallBlank.position)
        )
    )
    states = {
        b.blank_key: b
        for b in await db.scalars(
            select(RecallSessionBlank).where(RecallSessionBlank.session_id == s.id)
        )
    }
    errors = dict(
        (
            await db.execute(
                select(RecallSubmission.blank_key, func.count())
                .where(
                    RecallSubmission.user_id == s.user_id,
                    RecallSubmission.recall_id == s.recall_id,
                    RecallSubmission.correct.is_(False),
                )
                .group_by(RecallSubmission.blank_key)
            )
        ).all()
    )
    current = (
        s.queue[s.position]
        if s.phase in {"first", "retest"} and s.position < len(s.queue)
        else None
    )
    data = []
    for b in blanks:
        state = states[b.key]
        data.append(
            {
                "key": b.key,
                "label": b.label,
                "draft": state.draft if b.key == current else "",
                "round_error_count": state.round_error_count,
                "pass_error_count": state.pass_error_count,
                "historical_error_count": errors.get(b.key, 0),
                "needs_retest": state.needs_retest,
                "mastered": state.mastered,
                "hint": b.hints[state.pass_error_count - 1]
                if b.key == current and 0 < state.pass_error_count < 3
                else None,
            }
        )
    return {
        "id": str(s.id),
        "recall_id": str(r.id),
        "version_id": str(v.id),
        "subject_id": str(r.subject_id),
        "title": v.title,
        "type": v.type,
        "content": v.content,
        "phase": s.phase,
        "revision": s.revision,
        "current_key": current,
        "blanks": data,
        "slot_presentations": s.slot_presentations,
        "completed": sum(b.mastered for b in states.values()),
        "total": len(blanks),
        "read_only": not r.active,
        "is_current_version": v.id == r.published_version_id,
    }


async def create_session(db, user_id, rid):
    _r, v = await published(db, rid, write_guard=True)
    await lock(db, f"recall-start:{user_id}:{rid}")
    s = await db.scalar(
        select(RecallSession).where(
            RecallSession.user_id == user_id,
            RecallSession.recall_id == rid,
            RecallSession.phase != "completed",
        )
    )
    if not s:
        keys = list(
            await db.scalars(
                select(RecallBlank.key)
                .where(RecallBlank.version_id == v.id)
                .order_by(RecallBlank.position)
            )
        )
        s = RecallSession(user_id=user_id, recall_id=rid, version_id=v.id, queue=keys)
        db.add(s)
        await db.flush()
        db.add_all(
            [
                RecallSessionBlank(session_id=s.id, version_id=v.id, blank_key=k)
                for k in keys
            ]
        )
        await db.flush()
    return s


async def writable(db, sid, user_id, revision):
    enabled()
    if not get_settings().learning_enabled:
        raise fail(503, "LEARNING_DISABLED", "作答服务暂不可用")
    s = await owned(db, RecallSession, sid, user_id, write=True)
    await published(db, s.recall_id, write_guard=True)
    if s.revision != revision:
        raise fail(409, "RECALL_CHANGED", "训练已在其他页面更新，请重新加载")
    return s


async def submit(db, sid, user_id, payload, key):
    digest = fingerprint({**payload.model_dump(), "session_id": str(sid)})
    await lock(db, f"recall-submit:{user_id}:{key}")
    old = await db.scalar(
        select(RecallSubmission).where(
            RecallSubmission.user_id == user_id, RecallSubmission.idempotency_key == key
        )
    )
    if old:
        if old.request_hash != digest:
            raise fail(409, "IDEMPOTENCY_CONFLICT", "此提交标识已用于不同答案")
        return old.result
    s = await writable(db, sid, user_id, payload.revision)
    if s.phase not in {"first", "retest"} or s.queue[s.position] != payload.blank_key:
        raise fail(409, "RECALL_POSITION", "请作答当前空位")
    b = await db.get(RecallBlank, (s.version_id, payload.blank_key))
    state = await db.get(RecallSessionBlank, (s.id, b.key))
    rules = Normalization.model_validate(b.normalization)
    value = normalize(payload.answer, rules)
    if not value:
        raise fail(422, "EMPTY_ANSWER", "请输入答案")
    correct = value in {normalize(a, rules) for a in [b.answer, *b.acceptable_answers]}
    if correct or not get_settings().recall_semantic_grading_enabled:
        logging.getLogger("island").info("recall_rule_grading", extra={"fields": {
            "session_id": str(s.id), "blank_key": b.key, "correct": correct,
        }})
    if not correct and get_settings().recall_semantic_grading_enabled:
        version = await db.get(RecallVersion, s.version_id)
        try:
            correct = await grading.semantic_grade(
                content=version.content, blank=b, user_answer=payload.answer,
            )
        except grading.GradingUnavailable as exc:
            raise fail(503, "RECALL_GRADING_UNAVAILABLE", "暂时无法批改，请重试") from exc
    s.slot_presentations = {
        **s.slot_presentations,
        b.key: {"value": payload.answer, "outcome": "correct" if correct else "incorrect"},
    }
    hint_before = state.pass_error_count
    feedback = {
        "correct": correct,
        "blank_key": b.key,
        "hint": None,
        "answer": None,
        "needs_retest": False,
    }
    if correct:
        state.mastered = hint_before == 0
        state.needs_retest = not state.mastered
    else:
        state.round_error_count += 1
        state.pass_error_count += 1
        state.needs_retest = True
        if state.pass_error_count < 3:
            feedback["hint"] = b.hints[state.pass_error_count - 1]
        else:
            feedback["answer"] = b.answer
    feedback["needs_retest"] = state.needs_retest
    state.draft = ""
    if correct or state.pass_error_count == 3:
        s.position += 1
        if s.position == len(s.queue):
            await db.flush()
            pending = await db.scalar(
                select(func.count())
                .select_from(RecallSessionBlank)
                .where(
                    RecallSessionBlank.session_id == s.id,
                    RecallSessionBlank.mastered.is_(False),
                )
            )
            s.phase = "retest_ready" if pending else "completed"
            if not pending:
                s.completed_at = now()
    s.revision += 1
    s.updated_at = now()
    record = RecallSubmission(
        user_id=user_id,
        recall_id=s.recall_id,
        session_id=s.id,
        blank_key=b.key,
        idempotency_key=key,
        request_hash=digest,
        answer=payload.answer,
        normalized=value,
        correct=correct,
        hint_level=hint_before,
        result={},
    )
    db.add(record)
    await db.flush()
    result = {"feedback": feedback, "session": await session_data(db, s)}
    record.result = result
    await db.flush()
    return result


async def retest(db, sid, user_id, revision):
    s = await writable(db, sid, user_id, revision)
    if s.phase != "retest_ready":
        raise fail(409, "RECALL_PHASE", "当前尚未进入复测阶段")
    states = {
        b.blank_key: b
        for b in await db.scalars(
            select(RecallSessionBlank).where(RecallSessionBlank.session_id == s.id)
        )
    }
    keys = list(
        await db.scalars(
            select(RecallBlank.key)
            .where(RecallBlank.version_id == s.version_id)
            .order_by(RecallBlank.position)
        )
    )
    s.queue = [k for k in keys if not states[k].mastered]
    for k in s.queue:
        states[k].pass_error_count = 0
        states[k].draft = ""
    s.phase, s.position = "retest", 0
    s.slot_presentations = {}
    s.revision += 1
    s.updated_at = now()
    await db.flush()
    return await session_data(db, s)
